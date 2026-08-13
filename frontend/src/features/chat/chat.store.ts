import { computed, reactive, readonly, ref, shallowRef } from "vue";

import { chatApi, type Attachment, type ChatApi, type Conversation, type Message, type OperationEvent, type OperationStatus } from "../../api/chat";
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

export interface ChatStoreOptions {
  pollIntervalMs?: number;
  pollAttempts?: number;
}

const terminal = new Set<OperationStatus>(["completed", "failed", "cancelled", "waiting_stopped"]);
const safeErrorCode = (error: unknown): HttpErrorCode => error instanceof HttpError ? error.code : "network";

export const createChatStore = (api: ChatApi = chatApi, options: ChatStoreOptions = {}) => {
  const conversations = ref<Conversation[]>([]);
  const currentConversationId = ref<number | null>(null);
  const messages = ref<Message[]>([]);
  const operations = reactive(new Map<number, ActiveOperation>());
  const eventLog = ref<OperationEvent[]>([]);
  const connectionStatus = ref<ConnectionStatus>("connecting");
  const errorCode = ref<HttpErrorCode | null>(null);
  const attachmentsByMessage = reactive(new Map<number, Attachment[]>());
  const loading = ref(false);
  const disposed = ref(false);
  const pollIntervalMs = options.pollIntervalMs ?? 650;
  const pollAttempts = options.pollAttempts ?? 6;
  let conversationLoad: AbortController | null = null;
  let messageLoad: AbortController | null = null;
  let operationStream: AbortController | null = null;
  let loadToken = 0;
  let messageToken = 0;
  let temporaryMessageId = -1;
  const seenOperationEvents = new Map<string, Set<string>>();

  const recoverTerminalOperation = (conversationId: number, received: Message[]) => {
    if (operations.has(conversationId)) return;
    const last = received.at(-1);
    if (!last || last.role !== "system") return;
    const recoveredStatus = last.content === "The employee request was cancelled. Please retry."
      ? "cancelled"
      : last.content === "The employee could not complete the request. Please retry."
        || last.content === "The app restarted before the employee completed the request. Please retry."
        ? "failed"
        : null;
    if (!recoveredStatus) return;
    const user = [...received].reverse().find((item) => item.role === "user" && item.id < last.id);
    if (!user) return;
    operations.set(conversationId, {
      id: `recovered-${last.id}`,
      conversationId,
      status: recoveredStatus,
      lastEventId: 0,
      content: user.content,
      learningMode: false,
      userMessageId: user.id,
    });
  };

  const currentConversation = computed(() => conversations.value.find((item) => item.id === currentConversationId.value) ?? null);
  const operation = computed(() => currentConversationId.value === null ? null : operations.get(currentConversationId.value) ?? null);
  const isBusy = computed(() => Boolean(operation.value && !terminal.has(operation.value.status)));

  const replaceConversation = (item: Conversation) => {
    const index = conversations.value.findIndex((row) => row.id === item.id);
    if (index >= 0) conversations.value.splice(index, 1);
    conversations.value.unshift(item);
  };

  const loadMessages = async (conversationId: number, signal?: AbortSignal) => {
    const token = ++messageToken;
    messageLoad?.abort();
    const controller = new AbortController();
    messageLoad = controller;
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    try {
      const received = await api.listMessages(conversationId, controller.signal);
      if (disposed.value || token !== messageToken || currentConversationId.value !== conversationId) return received;
      messages.value = received;
      recoverTerminalOperation(conversationId, received);
      connectionStatus.value = "online";
      errorCode.value = null;
      return received;
    } catch (error) {
      if (!controller.signal.aborted && token === messageToken) {
        errorCode.value = safeErrorCode(error);
        connectionStatus.value = errorCode.value === "network" || errorCode.value === "timeout" ? "offline" : "error";
      }
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      if (messageLoad === controller) messageLoad = null;
    }
  };

  const delay = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });

  const reconcile = async (active: ActiveOperation, signal: AbortSignal) => {
    let previousSignature = "";
    for (let attempt = 0; attempt < pollAttempts && !signal.aborted; attempt += 1) {
      try {
        const received = await api.listMessages(active.conversationId, signal);
        if (currentConversationId.value === active.conversationId) messages.value = received;
        const signature = received.map((item) => `${item.id}:${item.role}:${item.content}`).join("|");
        const hasTerminal = received.some((item) => item.role !== "user" && item.id > active.userMessageId && item.content.length > 0);
        if (hasTerminal && signature !== previousSignature) {
          active.status = received.at(-1)?.role === "assistant" ? "completed" : "failed";
          return;
        }
        previousSignature = signature;
      } catch (error) {
        if (signal.aborted) return;
        errorCode.value = safeErrorCode(error);
      }
      if (attempt + 1 < pollAttempts) await delay(pollIntervalMs, signal).catch(() => undefined);
    }
    if (!terminal.has(active.status)) active.status = "waiting_stopped";
    connectionStatus.value = "offline";
  };

  const monitorOperation = async (active: ActiveOperation) => {
    operationStream?.abort();
    const controller = new AbortController();
    operationStream = controller;
    let sawFinal = false;
    const onEvent = (event: OperationEvent, eventId: number) => {
      if (event.operation_id !== active.id || eventId <= active.lastEventId) return;
      active.lastEventId = eventId;
      const signature = `${event.type}:${event.status}:${event.message_id ?? ""}`;
      const seen = seenOperationEvents.get(active.id) ?? new Set<string>();
      if (seen.has(signature)) return;
      seen.add(signature);
      seenOperationEvents.set(active.id, seen);
      active.status = event.status;
      eventLog.value.push(event);
      if (event.type === "final") sawFinal = true;
    };
    for (let attempt = 0; attempt < 2 && !controller.signal.aborted && !sawFinal; attempt += 1) {
      try {
        await api.streamOperation(active.id, {
          lastEventId: active.lastEventId || undefined,
          onEvent,
          signal: controller.signal,
        });
        if (!sawFinal) throw new HttpError("network", 0);
      } catch (error) {
        if (controller.signal.aborted) return;
        errorCode.value = safeErrorCode(error);
        if (attempt === 0) continue;
      }
    }
    if (controller.signal.aborted) return;
    if (sawFinal) {
      await loadMessages(active.conversationId).catch(() => undefined);
      connectionStatus.value = active.status === "completed" ? "online" : "error";
    } else {
      await reconcile(active, controller.signal);
    }
    if (operationStream === controller) operationStream = null;
  };

  const selectConversation = async (id: number) => {
    if (disposed.value) return;
    currentConversationId.value = id;
    operationStream?.abort();
    loading.value = true;
    try {
      await loadMessages(id);
      const active = operations.get(id);
      if (active && !terminal.has(active.status)) void monitorOperation(active);
    } finally {
      loading.value = false;
    }
  };

  const initialize = async () => {
    if (disposed.value) return;
    const token = ++loadToken;
    conversationLoad?.abort();
    conversationLoad = new AbortController();
    loading.value = true;
    connectionStatus.value = "connecting";
    try {
      const page = await api.listConversations(conversationLoad.signal);
      if (token !== loadToken || disposed.value) return;
      conversations.value = page.items;
      connectionStatus.value = "online";
      errorCode.value = null;
      const selection = currentConversationId.value && page.items.some((item) => item.id === currentConversationId.value)
        ? currentConversationId.value
        : page.items[0]?.id;
      if (selection) await selectConversation(selection);
    } catch (error) {
      if (!conversationLoad?.signal.aborted) {
        errorCode.value = safeErrorCode(error);
        connectionStatus.value = errorCode.value === "network" || errorCode.value === "timeout" ? "offline" : "error";
      }
    } finally {
      if (token === loadToken) loading.value = false;
    }
  };

  const createConversation = async (title = "新对话") => {
    if (disposed.value) return null;
    const item = await api.createConversation(title);
    replaceConversation(item);
    await selectConversation(item.id);
    return item;
  };

  const send = async (content: string, files: File[], learningMode: boolean) => {
    const conversationId = currentConversationId.value;
    const normalized = content.trim();
    if (!conversationId || !normalized || isBusy.value || disposed.value) return false;
    const accepting: ActiveOperation = {
      id: "accepting",
      conversationId,
      status: "running",
      lastEventId: 0,
      content: normalized,
      learningMode,
      userMessageId: 0,
    };
    operations.set(conversationId, accepting);
    const active = operations.get(conversationId)!;
    errorCode.value = null;
    connectionStatus.value = "connecting";
    const pendingId = temporaryMessageId--;
    messages.value.push({
      id: pendingId,
      conversation_id: conversationId,
      role: "user",
      content: normalized,
      created_at: new Date().toISOString(),
    });
    try {
      const uploaded: Attachment[] = [];
      for (const file of files) uploaded.push(await api.uploadAttachment(conversationId, file));
      if (uploaded.length) attachmentsByMessage.set(pendingId, uploaded);
      const accepted = await api.sendMessage(conversationId, {
        content: normalized,
        attachment_ids: uploaded.map((item) => item.id),
        learning_mode: learningMode,
      });
      active.id = accepted.operation_id;
      await loadMessages(conversationId);
      const persisted = [...messages.value].reverse().find((item) => item.role === "user" && item.content === normalized);
      active.userMessageId = persisted?.id ?? 0;
      if (persisted && uploaded.length) {
        attachmentsByMessage.set(persisted.id, uploaded);
        attachmentsByMessage.delete(pendingId);
      }
      void monitorOperation(active);
      return true;
    } catch (error) {
      messages.value = messages.value.filter((item) => item.id !== pendingId);
      operations.delete(conversationId);
      errorCode.value = safeErrorCode(error);
      connectionStatus.value = errorCode.value === "network" || errorCode.value === "timeout" ? "offline" : "error";
      return false;
    }
  };

  const retry = async () => {
    const previous = operation.value;
    if (!previous || !["failed", "cancelled"].includes(previous.status)) return false;
    operations.delete(previous.conversationId);
    return send(previous.content, [], previous.learningMode);
  };

  const stopWaiting = () => {
    const active = operation.value;
    if (!active || terminal.has(active.status)) return;
    operationStream?.abort();
    active.status = "waiting_stopped";
  };

  const attachmentsFor = (messageId: number) => attachmentsByMessage.get(messageId) ?? [];

  const dispose = () => {
    disposed.value = true;
    loadToken += 1;
    messageToken += 1;
    conversationLoad?.abort();
    messageLoad?.abort();
    operationStream?.abort();
    seenOperationEvents.clear();
  };

  return reactive({
    conversations,
    currentConversationId,
    currentConversation,
    messages,
    operation,
    eventLog,
    connectionStatus,
    errorCode,
    loading,
    isBusy,
    disposed: readonly(disposed),
    initialize,
    selectConversation,
    createConversation,
    send,
    retry,
    stopWaiting,
    attachmentsFor,
    dispose,
  });
};

export type ChatStore = ReturnType<typeof createChatStore>;
export const defaultChatStore = createChatStore();
