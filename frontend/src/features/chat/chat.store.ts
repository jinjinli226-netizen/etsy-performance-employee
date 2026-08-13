import { computed, reactive, readonly, ref } from "vue";

import {
  chatApi,
  type Attachment,
  type CandidateStatusItem,
  type ChatApi,
  type Conversation,
  type Message,
  type OperationEvent,
  type OperationStatus,
} from "../../api/chat";
import { HttpError, type HttpErrorCode } from "../../api/client";

export type ConnectionStatus = "connecting" | "online" | "offline" | "error";

export interface ActiveOperation {
  id: string;
  conversationId: number;
  status: OperationStatus;
  lastEventId: number;
  content: string;
  learningMode: boolean;
  userMessageId: number;
}

interface MonitorHandle { controller: AbortController; token: symbol; conversationId: number }
export interface ChatStoreOptions { pollIntervalMs?: number; pollAttempts?: number }

const terminal = new Set<OperationStatus>(["completed", "failed", "cancelled", "waiting_stopped"]);
const safeErrorCode = (error: unknown): HttpErrorCode => error instanceof HttpError ? error.code : "network";

export const createChatStore = (api: ChatApi = chatApi, options: ChatStoreOptions = {}) => {
  const conversations = ref<Conversation[]>([]);
  const currentConversationId = ref<number | null>(null);
  const messages = ref<Message[]>([]);
  const messageCache = reactive(new Map<number, Message[]>());
  const operations = reactive(new Map<number, ActiveOperation>());
  const eventLog = ref<OperationEvent[]>([]);
  const connectionStatus = ref<ConnectionStatus>("connecting");
  const errorCode = ref<HttpErrorCode | null>(null);
  const localAttachments = reactive(new Map<number, Attachment[]>());
  const candidateStatuses = reactive(new Map<string, CandidateStatusItem[]>());
  const loading = ref(false);
  const loadingMoreConversations = ref(false);
  const conversationTotal = ref(0);
  const hasMoreConversations = computed(() => conversations.value.length < conversationTotal.value);
  const disposed = ref(false);
  const pollIntervalMs = options.pollIntervalMs ?? 650;
  const pollAttempts = options.pollAttempts ?? 6;
  const loadControllers = new Map<number, AbortController>();
  const loadTokens = new Map<number, symbol>();
  const monitors = new Map<string, MonitorHandle>();
  let conversationLoad: AbortController | null = null;
  let conversationToken: symbol | null = null;
  let temporaryMessageId = -1;

  const currentConversation = computed(() => conversations.value.find((item) => item.id === currentConversationId.value) ?? null);
  const operation = computed(() => currentConversationId.value === null ? null : operations.get(currentConversationId.value) ?? null);
  const isBusy = computed(() => Boolean(operation.value && !terminal.has(operation.value.status)));
  const learningStatuses = computed(() => operation.value?.learningMode ? candidateStatuses.get(operation.value.id) ?? [] : []);
  const operationFor = (conversationId: number) => operations.get(conversationId);

  const recoverOperation = (conversationId: number, received: Message[]) => {
    const existing = operations.get(conversationId);
    if (existing && !terminal.has(existing.status)) return;
    const user = [...received].reverse().find((item) => item.role === "user" && (
      ["failed", "cancelled"].includes(item.operation_status ?? "")
      || (item.learning_mode && item.operation_status === "completed")
    ));
    if (!user) return;
    operations.set(conversationId, {
      id: user.operation_id ?? `recovered-${user.id}`,
      conversationId,
      status: user.operation_status as "completed" | "failed" | "cancelled",
      lastEventId: 0,
      content: user.content,
      learningMode: Boolean(user.learning_mode),
      userMessageId: user.id,
    });
  };

  const cacheMessages = (conversationId: number, received: Message[]) => {
    messageCache.set(conversationId, received);
    recoverOperation(conversationId, received);
    if (currentConversationId.value === conversationId) messages.value = received;
  };

  const loadMessages = async (conversationId: number, external?: AbortSignal) => {
    loadControllers.get(conversationId)?.abort();
    const controller = new AbortController();
    const token = Symbol("message-load");
    loadControllers.set(conversationId, controller);
    loadTokens.set(conversationId, token);
    const abort = () => controller.abort();
    external?.addEventListener("abort", abort, { once: true });
    try {
      const received = await api.listMessages(conversationId, controller.signal);
      if (disposed.value || loadTokens.get(conversationId) !== token) return received;
      cacheMessages(conversationId, received);
      connectionStatus.value = "online";
      errorCode.value = null;
      return received;
    } catch (error) {
      if (!controller.signal.aborted && loadTokens.get(conversationId) === token) {
        errorCode.value = safeErrorCode(error);
        connectionStatus.value = ["network", "timeout"].includes(errorCode.value) ? "offline" : "error";
      }
      throw error;
    } finally {
      external?.removeEventListener("abort", abort);
      if (loadTokens.get(conversationId) === token) {
        loadTokens.delete(conversationId);
        loadControllers.delete(conversationId);
      }
    }
  };

  const delay = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });

  const reconcile = async (active: ActiveOperation, handle: MonitorHandle) => {
    for (let attempt = 0; attempt < pollAttempts && !handle.controller.signal.aborted; attempt += 1) {
      try {
        const received = await api.listMessages(active.conversationId, handle.controller.signal);
        if (monitors.get(active.id)?.token !== handle.token) return;
        cacheMessages(active.conversationId, received);
        const terminalMessage = [...received].reverse().find((item) => item.operation_id === active.id && item.role !== "user");
        if (terminalMessage) {
          active.status = terminalMessage.role === "assistant" ? "completed" : (terminalMessage.operation_status as "failed" | "cancelled") || "failed";
          return;
        }
      } catch (error) {
        if (handle.controller.signal.aborted) return;
        errorCode.value = safeErrorCode(error);
      }
      if (attempt + 1 < pollAttempts) await delay(pollIntervalMs, handle.controller.signal).catch(() => undefined);
    }
    if (monitors.get(active.id)?.token === handle.token && !terminal.has(active.status)) active.status = "waiting_stopped";
  };

  const loadLearningStatuses = async (active: ActiveOperation, signal: AbortSignal) => {
    if (!active.learningMode || !/^[0-9a-f-]{36}$/.test(active.id)) return;
    try {
      candidateStatuses.set(active.id, await api.listCandidateStatuses(active.id, signal));
    } catch {
      // Learning status is supplementary; chat completion stays intact.
    }
  };

  const monitorOperation = async (active: ActiveOperation) => {
    const old = monitors.get(active.id);
    if (old) old.controller.abort();
    const handle: MonitorHandle = { controller: new AbortController(), token: Symbol("operation-monitor"), conversationId: active.conversationId };
    monitors.set(active.id, handle);
    let sawFinal = false;
    const onEvent = (event: OperationEvent, eventId: number) => {
      if (monitors.get(active.id)?.token !== handle.token || event.operation_id !== active.id || eventId <= active.lastEventId) return;
      active.lastEventId = eventId;
      active.status = event.status;
      eventLog.value.push(event);
      if (event.type === "final") sawFinal = true;
    };
    for (let attempt = 0; attempt < 2 && !handle.controller.signal.aborted && !sawFinal; attempt += 1) {
      try {
        await api.streamOperation(active.id, { lastEventId: active.lastEventId || undefined, onEvent, signal: handle.controller.signal });
        if (!sawFinal) throw new HttpError("network", 0);
      } catch (error) {
        if (handle.controller.signal.aborted || monitors.get(active.id)?.token !== handle.token) return;
        errorCode.value = safeErrorCode(error);
      }
    }
    if (handle.controller.signal.aborted || monitors.get(active.id)?.token !== handle.token) return;
    if (sawFinal) {
      await loadMessages(active.conversationId).catch(() => undefined);
      await loadLearningStatuses(active, handle.controller.signal);
    } else {
      await reconcile(active, handle);
    }
    if (monitors.get(active.id)?.token === handle.token) monitors.delete(active.id);
  };

  const selectConversation = async (id: number) => {
    if (disposed.value) return;
    currentConversationId.value = id;
    messages.value = messageCache.get(id) ?? [];
    loading.value = !messageCache.has(id);
    try {
      await loadMessages(id);
      const active = operations.get(id);
      if (active && !terminal.has(active.status) && !monitors.has(active.id)) void monitorOperation(active);
      if (active?.learningMode && terminal.has(active.status)) await loadLearningStatuses(active, new AbortController().signal);
    } finally {
      loading.value = false;
    }
  };

  const initialize = async () => {
    if (disposed.value) return;
    conversationLoad?.abort();
    conversationLoad = new AbortController();
    const token = Symbol("conversation-load");
    conversationToken = token;
    loading.value = true;
    connectionStatus.value = "connecting";
    try {
      const page = await api.listConversations(conversationLoad.signal);
      if (disposed.value || conversationToken !== token) return;
      conversations.value = page.items;
      conversationTotal.value = page.total;
      connectionStatus.value = "online";
      const selection = currentConversationId.value && page.items.some((item) => item.id === currentConversationId.value)
        ? currentConversationId.value : page.items[0]?.id;
      if (selection) await selectConversation(selection);
    } catch (error) {
      if (!conversationLoad.signal.aborted) {
        errorCode.value = safeErrorCode(error);
        connectionStatus.value = ["network", "timeout"].includes(errorCode.value) ? "offline" : "error";
      }
    } finally {
      if (conversationToken === token) loading.value = false;
    }
  };

  const loadMoreConversations = async () => {
    if (disposed.value || loadingMoreConversations.value || !hasMoreConversations.value) return false;
    loadingMoreConversations.value = true;
    try {
      const page = await api.listConversations(undefined, 100, conversations.value.length);
      const known = new Set(conversations.value.map((item) => item.id));
      conversations.value = [...conversations.value, ...page.items.filter((item) => !known.has(item.id))];
      conversationTotal.value = page.total;
      errorCode.value = null;
      return true;
    } catch (error) {
      errorCode.value = safeErrorCode(error);
      return false;
    } finally {
      loadingMoreConversations.value = false;
    }
  };

  const createConversation = async (title = "新对话") => {
    const item = await api.createConversation(title);
    const existed = conversations.value.some((row) => row.id === item.id);
    conversations.value = [item, ...conversations.value.filter((row) => row.id !== item.id)];
    if (!existed) conversationTotal.value += 1;
    messageCache.set(item.id, []);
    await selectConversation(item.id);
    return item;
  };

  const beginOperation = async (active: ActiveOperation, acceptedId: string) => {
    active.id = acceptedId;
    await loadMessages(active.conversationId).catch(() => undefined);
    const persisted = [...(messageCache.get(active.conversationId) ?? [])].reverse().find((item) => item.operation_id === acceptedId)
      ?? [...(messageCache.get(active.conversationId) ?? [])].reverse().find((item) => item.role === "user" && item.content === active.content);
    active.userMessageId = persisted?.id ?? active.userMessageId;
    void monitorOperation(active);
  };

  const send = async (content: string, files: File[], learningMode: boolean) => {
    const conversationId = currentConversationId.value;
    const normalized = content.trim();
    if (!conversationId || !normalized || disposed.value || (operations.get(conversationId) && !terminal.has(operations.get(conversationId)!.status))) return false;
    const active: ActiveOperation = { id: "accepting", conversationId, status: "running", lastEventId: 0, content: normalized, learningMode, userMessageId: 0 };
    operations.set(conversationId, active);
    const storedActive = operations.get(conversationId)!;
    const pendingId = temporaryMessageId--;
    const pending: Message = { id: pendingId, conversation_id: conversationId, role: "user", content: normalized, created_at: new Date().toISOString() };
    cacheMessages(conversationId, [...(messageCache.get(conversationId) ?? []), pending]);
    try {
      if (files.length) localAttachments.set(pendingId, files.map((file, index) => ({
        id: pendingId - index,
        conversation_id: conversationId,
        filename: file.name,
        media_type: file.type,
        created_at: pending.created_at,
      })));
      const input = { content: normalized, attachment_ids: [], learning_mode: learningMode };
      const accepted = files.length
        ? await api.sendMessageBatch(conversationId, input, files)
        : await api.sendMessage(conversationId, input);
      await beginOperation(storedActive, accepted.operation_id);
      return true;
    } catch (error) {
      cacheMessages(conversationId, (messageCache.get(conversationId) ?? []).filter((item) => item.id !== pendingId));
      operations.delete(conversationId);
      errorCode.value = safeErrorCode(error);
      return false;
    }
  };

  const retry = async () => {
    const previous = operation.value;
    if (!previous || !["failed", "cancelled"].includes(previous.status) || previous.userMessageId <= 0) return false;
    try {
      const accepted = await api.retryMessage(previous.conversationId, previous.userMessageId);
      const active: ActiveOperation = { ...previous, id: accepted.operation_id, status: "running", lastEventId: 0, userMessageId: 0 };
      operations.set(previous.conversationId, active);
      await beginOperation(operations.get(previous.conversationId)!, accepted.operation_id);
      return true;
    } catch (error) {
      errorCode.value = safeErrorCode(error);
      connectionStatus.value = ["network", "timeout"].includes(errorCode.value) ? "offline" : "error";
      operations.set(previous.conversationId, previous);
      return false;
    }
  };

  const stopWaiting = () => {
    const active = operation.value;
    if (!active || terminal.has(active.status)) return;
    monitors.get(active.id)?.controller.abort();
    monitors.delete(active.id);
    active.status = "waiting_stopped";
  };

  const attachmentsFor = (messageId: number) => {
    const message = messages.value.find((item) => item.id === messageId);
    return message?.attachments ?? localAttachments.get(messageId) ?? [];
  };

  const dispose = () => {
    disposed.value = true;
    conversationLoad?.abort();
    loadControllers.forEach((controller) => controller.abort());
    monitors.forEach(({ controller }) => controller.abort());
    loadControllers.clear();
    monitors.clear();
  };

  return reactive({
    conversations, currentConversationId, currentConversation, messages, operation, eventLog,
    connectionStatus, errorCode, loading, loadingMoreConversations, hasMoreConversations, isBusy, learningStatuses, disposed: readonly(disposed),
    initialize, loadMoreConversations, selectConversation, createConversation, send, retry, stopWaiting, attachmentsFor,
    operationFor, dispose,
  });
};

export type ChatStore = ReturnType<typeof createChatStore>;
export const defaultChatStore = createChatStore();
