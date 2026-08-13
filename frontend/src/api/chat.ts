import { apiRequest, openEventStream } from "./client";

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationPage {
  items: Conversation[];
  total: number;
  limit: number;
  offset: number;
}

export type MessageRole = "user" | "assistant" | "system";

export interface Message {
  id: number;
  conversation_id: number;
  role: MessageRole;
  content: string;
  created_at: string;
}

export interface Attachment {
  id: number;
  conversation_id: number;
  filename: string;
  media_type: string;
  created_at: string;
}

export interface SendMessageInput {
  content: string;
  attachment_ids: number[];
  learning_mode: boolean;
}

export interface OperationAccepted {
  operation_id: string;
  status: "running";
}

export type OperationStatus = "running" | "completed" | "failed" | "cancelled" | "waiting_stopped";
export type OperationEvent = {
  type: "progress" | "final";
  status: Exclude<OperationStatus, "waiting_stopped">;
  operation_id: string;
  message_id?: number;
};

export interface ChatApi {
  listConversations(signal?: AbortSignal): Promise<ConversationPage>;
  createConversation(title: string, signal?: AbortSignal): Promise<Conversation>;
  listMessages(conversationId: number, signal?: AbortSignal): Promise<Message[]>;
  uploadAttachment(conversationId: number, file: File, signal?: AbortSignal): Promise<Attachment>;
  sendMessage(conversationId: number, input: SendMessageInput, signal?: AbortSignal): Promise<OperationAccepted>;
  streamOperation(
    operationId: string,
    options: { lastEventId?: number; onEvent: (event: OperationEvent, id: number) => void; signal: AbortSignal },
  ): Promise<void>;
}

const isOperationEvent = (value: unknown): value is OperationEvent => {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return (event.type === "progress" || event.type === "final")
    && typeof event.operation_id === "string"
    && ["running", "completed", "failed", "cancelled"].includes(String(event.status));
};

export const chatApi: ChatApi = {
  listConversations: (signal) => apiRequest<ConversationPage>("/conversations?limit=100&offset=0", { signal }),
  createConversation: (title, signal) => apiRequest<Conversation>("/conversations", { method: "POST", body: { title }, signal }),
  listMessages: (conversationId, signal) => apiRequest<Message[]>(`/conversations/${conversationId}/messages`, { signal }),
  uploadAttachment: (conversationId, file, signal) => {
    const form = new FormData();
    form.set("conversation_id", String(conversationId));
    form.set("file", file, file.name);
    return apiRequest<Attachment>("/attachments", { method: "POST", body: form, signal, timeoutMs: 60_000 });
  },
  sendMessage: (conversationId, input, signal) => apiRequest<OperationAccepted>(`/conversations/${conversationId}/messages`, {
    method: "POST",
    body: input,
    signal,
    timeoutMs: 30_000,
  }),
  streamOperation: (operationId, options) => openEventStream(`/events/${encodeURIComponent(operationId)}`, {
    ...options,
    onEvent: (value, id) => {
      if (isOperationEvent(value) && value.operation_id === operationId) options.onEvent(value, id);
    },
  }),
};
