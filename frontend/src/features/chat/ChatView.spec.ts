import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Attachment,
  ChatApi,
  Conversation,
  Message,
  OperationEvent,
  SendMessageInput,
} from "../../api/chat";
import ChatView from "../../views/ChatView.vue";
import MessageComposer from "./MessageComposer.vue";
import MessageStream from "./MessageStream.vue";
import { createChatStore } from "./chat.store";
import { openEventStream } from "../../api/client";

const conversations: Conversation[] = [
  { id: 1, title: "舞台服标题规范", created_at: "2026-08-12T08:00:00Z", updated_at: "2026-08-12T08:00:00Z" },
  { id: 2, title: "尺码与买家须知", created_at: "2026-08-12T07:00:00Z", updated_at: "2026-08-12T07:00:00Z" },
];

const message = (id: number, conversationId: number, role: Message["role"], content: string): Message => ({
  id,
  conversation_id: conversationId,
  role,
  content,
  created_at: `2026-08-12T08:00:0${id}Z`,
});

class FakeChatApi implements ChatApi {
  conversations = [...conversations];
  messages = new Map<number, Message[]>([[1, [message(1, 1, "assistant", "我已准备好继续学习。")]], [2, []]]);
  sent: Array<{ conversationId: number; input: SendMessageInput }> = [];
  uploaded: File[] = [];
  events: OperationEvent[] = [
    { type: "progress", status: "running", operation_id: "op-1" },
    { type: "final", status: "completed", operation_id: "op-1", message_id: 3 },
  ];
  streamFailures = 0;
  streamCalls: number[] = [];
  retries: number[] = [];
  candidateStatuses: Array<{ id: string; status: "proposed" | "testing" | "active" | "rejected" | "rolled_back" }> = [];
  sendGate: Promise<void> | undefined;

  async listConversations() {
    return { items: this.conversations, total: this.conversations.length, limit: 100, offset: 0 };
  }

  async createConversation(title: string) {
    const item: Conversation = { id: 3, title, created_at: "2026-08-12T09:00:00Z", updated_at: "2026-08-12T09:00:00Z" };
    this.conversations.unshift(item);
    this.messages.set(item.id, []);
    return item;
  }

  async listMessages(conversationId: number) {
    return [...(this.messages.get(conversationId) ?? [])];
  }

  async retryMessage(conversationId: number, messageId: number) {
    this.retries.push(messageId);
    return { operation_id: `retry-${conversationId}-${messageId}`, status: "running" as const };
  }

  async listCandidateStatuses() {
    return this.candidateStatuses;
  }

  async uploadAttachment(conversationId: number, file: File) {
    this.uploaded.push(file);
    return {
      id: 100 + this.uploaded.length,
      conversation_id: conversationId,
      filename: file.name,
      media_type: file.type,
      created_at: "2026-08-12T09:00:00Z",
    } satisfies Attachment;
  }

  async sendMessage(conversationId: number, input: SendMessageInput) {
    await this.sendGate;
    this.sent.push({ conversationId, input });
    const rows = this.messages.get(conversationId) ?? [];
    rows.push(message(2, conversationId, "user", input.content));
    this.messages.set(conversationId, rows);
    return { operation_id: "op-1", status: "running" as const };
  }

  async streamOperation(
    _operationId: string,
    options: { lastEventId?: number; onEvent: (event: OperationEvent, id: number) => void; signal: AbortSignal },
  ) {
    this.streamCalls.push(options.lastEventId ?? 0);
    if (this.streamFailures > 0) {
      this.streamFailures -= 1;
      throw new TypeError("network");
    }
    for (const [index, event] of this.events.entries()) {
      if (options.signal.aborted) return;
      options.onEvent(event, index + 1);
      if (event.type === "final" && event.status === "completed") {
        const rows = this.messages.get(1) ?? [];
        if (!rows.some((item) => item.id === 3)) rows.push(message(3, 1, "assistant", "这是原创 Listing 建议。"));
      }
    }
  }
}

const wrappers: ReturnType<typeof mount>[] = [];
const renderChat = async (api = new FakeChatApi()) => {
  const store = createChatStore(api, { pollIntervalMs: 1, pollAttempts: 2 });
  const wrapper = mount(ChatView, { props: { store }, attachTo: document.body });
  wrappers.push(wrapper);
  await flushPromises();
  return { wrapper, store, api };
};

beforeEach(() => {
  class TestURL extends URL {
    static createObjectURL = vi.fn((file: File) => `blob:${file.name}`);
    static revokeObjectURL = vi.fn();
  }
  vi.stubGlobal("URL", TestURL);
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("persistent chat workspace", () => {
  it("loads, selects, and creates conversations without losing their titles", async () => {
    const { wrapper, store } = await renderChat();
    expect(wrapper.text()).toContain("舞台服标题规范");
    expect(wrapper.text()).toContain("我已准备好继续学习。");

    await wrapper.get('[data-testid="conversation-2"]').trigger("click");
    await flushPromises();
    expect(store.currentConversationId).toBe(2);

    await wrapper.get('[data-testid="new-conversation"]').trigger("click");
    await flushPromises();
    expect(store.currentConversation?.title).toBe("新对话");
  });

  it("sends ordinary text once, renders progress/final, and restores composer focus", async () => {
    const { wrapper, api, store } = await renderChat();
    const textarea = wrapper.get<HTMLTextAreaElement>('[data-testid="message-input"]');
    await textarea.setValue("请为这件亮片舞台服写标题");
    await textarea.trigger("keydown", { key: "Enter", shiftKey: false, isComposing: false });
    await flushPromises();

    for (let attempt = 0; attempt < 5 && wrapper.get('[data-testid="message-input"]').attributes("disabled") !== undefined; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      await flushPromises();
    }

    expect(api.sent).toHaveLength(1);
    expect(store.operation?.status).toBe("completed");
    expect(api.sent[0].input).toEqual({ content: "请为这件亮片舞台服写标题", attachment_ids: [], learning_mode: false });
    expect(wrapper.text()).toContain("这是原创 Listing 建议。");
    expect(document.activeElement).toBe(textarea.element);
  });

  it("uploads an image, workbook, and safe file before sending while rejecting a second image", async () => {
    const { wrapper, api } = await renderChat();
    const input = wrapper.get<HTMLInputElement>('[data-testid="attachment-input"]');
    const files = [
      new File(["image"], "front.png", { type: "image/png" }),
      new File(["PK"], "listing.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }),
      new File(["notes"], "notes.txt", { type: "text/plain" }),
    ];
    Object.defineProperty(input.element, "files", { value: files, configurable: true });
    await input.trigger("change");

    const second = new File(["back"], "back.webp", { type: "image/webp" });
    Object.defineProperty(input.element, "files", { value: [second], configurable: true });
    await input.trigger("change");
    expect(wrapper.text()).toContain("一次最多添加 1 张图片");

    await wrapper.get<HTMLTextAreaElement>('[data-testid="message-input"]').setValue("检查这些资料");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    await flushPromises();

    expect(api.uploaded.map((file) => file.name)).toEqual(["front.png", "listing.xlsx", "notes.txt"]);
    expect(api.sent[0].input.attachment_ids).toEqual([101, 102, 103]);
  });

  it("requires an explicit Etsy listing URL in teaching mode and resets the mode after success", async () => {
    const { wrapper, api } = await renderChat();
    await wrapper.get('[data-testid="learning-toggle"]').setValue(true);
    await wrapper.get<HTMLTextAreaElement>('[data-testid="message-input"]').setValue("学习这个竞品");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    expect(wrapper.text()).toContain("请在消息中加入 Etsy 商品链接");
    expect(api.sent).toHaveLength(0);

    await wrapper.get<HTMLTextAreaElement>('[data-testid="message-input"]').setValue("学习 https://www.etsy.com/listing/123456/sample");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    await flushPromises();
    expect(api.sent[0].input.learning_mode).toBe(true);
    expect((wrapper.get('[data-testid="learning-toggle"]').element as HTMLInputElement).checked).toBe(false);
  });

  it("prevents duplicate sends while one operation is being accepted", async () => {
    const api = new FakeChatApi();
    let release!: () => void;
    api.sendGate = new Promise<void>((resolve) => { release = resolve; });
    const { wrapper } = await renderChat(api);
    await wrapper.get<HTMLTextAreaElement>('[data-testid="message-input"]').setValue("只发送一次");
    const send = wrapper.get('[data-testid="send-message"]');
    await send.trigger("click");
    await send.trigger("click");
    expect(send.attributes("disabled")).toBeDefined();
    release();
    await flushPromises();
    expect(api.sent).toHaveLength(1);
  });

  it("keeps an accepted operation recoverable when the user switches conversations during send", async () => {
    const api = new FakeChatApi();
    let release!: () => void;
    api.sendGate = new Promise<void>((resolve) => { release = resolve; });
    const { store } = await renderChat(api);
    const sending = store.send("后台继续处理", [], false);
    await store.selectConversation(2);
    release();
    expect(await sending).toBe(true);
    expect(store.currentConversationId).toBe(2);

    api.sendGate = undefined;
    await store.selectConversation(1);
    expect(store.messages.some((item) => item.content === "后台继续处理")).toBe(true);
  });

  it("monitors operations in two conversations independently without stale cleanup", async () => {
    const api = new FakeChatApi();
    api.sendMessage = async (conversationId, input) => {
      api.sent.push({ conversationId, input });
      return { operation_id: `op-${conversationId}`, status: "running" as const };
    };
    let releaseFirst!: () => void;
    let call = 0;
    api.streamOperation = async (operationId, options) => {
      api.streamCalls.push(options.lastEventId ?? 0);
      call += 1;
      if (call === 1) await new Promise<void>((resolve) => { releaseFirst = resolve; });
      options.onEvent({ type: "final", status: "completed", operation_id: operationId, message_id: 100 + call }, 100 + call);
    };
    const { store } = await renderChat(api);
    expect(await store.send("A operation", [], false)).toBe(true);
    await store.selectConversation(2);
    expect(await store.send("B operation", [], false)).toBe(true);
    releaseFirst();
    await flushPromises();
    expect(store.operationFor(1)?.status).toBe("completed");
    expect(store.operationFor(2)?.status).toBe("completed");
  });

  it("reconnects SSE exactly once with the last event id then falls back to bounded message polling", async () => {
    const reconnecting = new FakeChatApi();
    let partialFailure = true;
    reconnecting.streamOperation = async (_operationId, options) => {
      reconnecting.streamCalls.push(options.lastEventId ?? 0);
      if (partialFailure) {
        partialFailure = false;
        options.onEvent({ type: "progress", status: "running", operation_id: "op-1" }, 7);
        throw new TypeError("network");
      }
      options.onEvent({ type: "progress", status: "running", operation_id: "op-1" }, 8);
      options.onEvent({ type: "final", status: "failed", operation_id: "op-1", message_id: 3 }, 9);
    };
    const first = await renderChat(reconnecting);
    await first.store.send("网络重试", [], false);
    await flushPromises();
    expect(reconnecting.streamCalls).toEqual([0, 7]);
    expect(first.store.operation?.status).toBe("failed");

    const polling = new FakeChatApi();
    polling.streamFailures = 2;
    const second = await renderChat(polling);
    const listSpy = vi.spyOn(polling, "listMessages");
    await second.store.send("轮询恢复", [], false);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(polling.streamCalls).toHaveLength(2);
    expect(listSpy.mock.calls.length).toBeLessThanOrEqual(4);
  });

  it("deduplicates event ids, hides internal frames, and offers retry after failure or cancellation", async () => {
    const wrapper = mount(MessageStream, {
      props: {
        messages: [
          message(1, 1, "assistant", "可见回答\n{\"event\":\"learning_batch\",\"payload\":{}}"),
          message(2, 1, "system", "The employee could not complete the request. Please retry."),
        ],
        operationStatus: "cancelled",
        attachmentsFor: () => [],
      },
    });
    wrappers.push(wrapper);
    expect(wrapper.text()).toContain("可见回答");
    expect(wrapper.text()).not.toContain("learning_batch");
    expect(wrapper.text()).not.toContain("The employee");
    expect(wrapper.text()).toContain("本次请求已取消");
  });

  it("keeps the store across route-style remounts and disposes streams and object URLs explicitly", async () => {
    const api = new FakeChatApi();
    const store = createChatStore(api);
    const first = mount(ChatView, { props: { store } });
    await flushPromises();
    first.unmount();
    const second = mount(ChatView, { props: { store } });
    wrappers.push(second);
    await flushPromises();
    expect(second.text()).toContain("我已准备好继续学习。");
    store.dispose();
    expect(store.disposed).toBe(true);
  });

  it("recovers a persisted safe failure after remount and retries the preceding user message", async () => {
    const api = new FakeChatApi();
    api.messages.set(1, [
      { ...message(10, 1, "user", "恢复后重试我"), operation_status: "failed", operation_id: "failed-op" },
      message(11, 1, "system", "The app restarted before the employee completed the request. Please retry."),
    ]);
    const { store } = await renderChat(api);
    expect(store.operation?.status).toBe("failed");
    await store.retry();
    await flushPromises();
    expect(api.retries).toEqual([10]);
  });

  it("recovers attachment metadata and retries without uploading the files again", async () => {
    const api = new FakeChatApi();
    api.messages.set(1, [
      { ...message(20, 1, "user", "附件重试"), operation_status: "failed", operation_id: "failed-op", attachments: [{ id: 81, conversation_id: 1, filename: "notes.txt", media_type: "text/plain", created_at: "2026-08-12T08:00:00Z" }] },
      message(21, 1, "system", "The employee could not complete the request. Please retry."),
    ]);
    const { wrapper, store } = await renderChat(api);
    expect(wrapper.text()).toContain("notes.txt");
    await store.retry();
    expect(api.retries).toEqual([20]);
    expect(api.uploaded).toHaveLength(0);
  });

  it("uses the same strict Etsy URL rules as the backend", async () => {
    for (const invalid of [
      "http://www.etsy.com/listing/123",
      "https://www.etsy.com.evil/listing/123",
      "https://user@www.etsy.com/listing/123",
      "https://www.etsy.com:443/listing/123",
      "https://www.etsy.com/listing/123.evil",
      "https://www.etsy.com/listing/123/slug#x",
    ]) {
      const { wrapper, api } = await renderChat();
      await wrapper.get('[data-testid="learning-toggle"]').setValue(true);
      const input = wrapper.get<HTMLInputElement>('[data-testid="message-input"]');
      await input.setValue(invalid);
      await wrapper.get('[data-testid="send-message"]').trigger("click");
      expect(api.sent).toHaveLength(0);
    }
    const { wrapper, api } = await renderChat();
    await wrapper.get('[data-testid="learning-toggle"]').setValue(true);
    const input = wrapper.get<HTMLInputElement>('[data-testid="message-input"]');
    await input.setValue("https://www.etsy.com/listing/123/valid-slug?utm_source=x");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    await flushPromises();
    expect(api.sent).toHaveLength(1);
  });

  it("renders only safe candidate lifecycle statuses for learning operations", async () => {
    const api = new FakeChatApi();
    api.candidateStatuses = [
      { id: "kc-1", status: "proposed" },
      { id: "kc-2", status: "active" },
      { id: "kc-3", status: "rejected" },
      { id: "kc-4", status: "rolled_back" },
    ];
    api.sendMessage = async (conversationId, input) => {
      api.sent.push({ conversationId, input });
      return { operation_id: "11111111-1111-4111-8111-111111111111", status: "running" as const };
    };
    api.streamOperation = async (operationId, options) => {
      options.onEvent({ type: "final", status: "completed", operation_id: operationId, message_id: 3 }, 3);
    };
    const { wrapper } = await renderChat(api);
    await wrapper.get('[data-testid="learning-toggle"]').setValue(true);
    await wrapper.get<HTMLInputElement>('[data-testid="message-input"]').setValue("learn https://etsy.com/listing/123");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("待审批");
    expect(wrapper.text()).toContain("已学习");
    expect(wrapper.text()).toContain("已隔离");
    expect(wrapper.text()).toContain("已撤销");
    expect(wrapper.text()).not.toContain("kc-1");
  });

  it("reloads safe learning status from a persisted completed teaching message", async () => {
    const api = new FakeChatApi();
    api.candidateStatuses = [{ id: "kc-safe", status: "active" }];
    api.messages.set(1, [
      { ...message(30, 1, "user", "learn https://etsy.com/listing/123"), operation_id: "11111111-1111-4111-8111-111111111111", operation_status: "completed", learning_mode: true },
      { ...message(31, 1, "assistant", "学习完成"), operation_id: "11111111-1111-4111-8111-111111111111", operation_status: "completed" },
    ]);
    const { wrapper } = await renderChat(api);
    await flushPromises();
    expect(wrapper.text()).toContain("已学习");
    expect(wrapper.text()).not.toContain("kc-safe");
  });
});

describe("message composer keyboard and attachment safety", () => {
  it("sends on Enter, preserves Shift+Enter and IME composition, and revokes removed previews", async () => {
    const wrapper = mount(MessageComposer, { props: { busy: false } });
    wrappers.push(wrapper);
    const textarea = wrapper.get<HTMLTextAreaElement>('textarea');
    await textarea.setValue("舞台服");
    await textarea.trigger("keydown", { key: "Enter", shiftKey: true, isComposing: false });
    expect(wrapper.emitted("submit")).toBeUndefined();
    await textarea.trigger("keydown", { key: "Enter", shiftKey: false, isComposing: true });
    expect(wrapper.emitted("submit")).toBeUndefined();
    await textarea.trigger("keydown", { key: "Enter", shiftKey: false, isComposing: false });
    expect(wrapper.emitted("submit")).toHaveLength(1);

    const input = wrapper.get<HTMLInputElement>('input[type="file"]');
    const image = new File(["x"], "front.png", { type: "image/png" });
    Object.defineProperty(input.element, "files", { value: [image], configurable: true });
    await input.trigger("change");
    await nextTick();
    await wrapper.get('[aria-label="移除 front.png"]').trigger("click");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:front.png");
  });
});

describe("chat API event stream", () => {
  it("parses bounded SSE frames and sends Last-Event-ID on reconnect", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: 12\nevent: operation\ndata: {"type":"progress","status":"running","operation_id":"op-1"}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Last-Event-ID")).toBe("11");
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const received: Array<{ value: unknown; id: number }> = [];
    await openEventStream("/events/op-1", {
      lastEventId: 11,
      signal: new AbortController().signal,
      onEvent: (value, id) => received.push({ value, id }),
    });
    expect(received).toEqual([{ value: { type: "progress", status: "running", operation_id: "op-1" }, id: 12 }]);
  });
});
