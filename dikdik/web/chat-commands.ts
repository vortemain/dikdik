// The Commands switch in a chat: off by default, remembered per chat in this browser.
// When on, the chat's requests ask the server to run commands on this computer, in the chat's
// own folder, and every command waits for the user's approval in the usual confirmation card.

export const CHAT_COMMANDS_KEY = "dikdik.chatCommands.v1";
export const CHAT_COMMANDS_LIMIT = 500;
export const CHAT_COMMANDS_OFF = "Let Dikdik run commands for this chat. They run on this computer, and Dikdik asks you before each one.";
export const CHAT_COMMANDS_ON = "Commands on: they run on this computer, and Dikdik asks you before each one.";

type Storage = { getItem(key: string): string | null; setItem(key: string, value: string): void };

export interface ChatCommands {
  /** The choice for a chat; `null` is the new chat not yet sent. */
  get(chat: string | null): boolean;
  set(chat: string | null, on: boolean): void;
  /** The new chat was created: it keeps the choice made before its first message. */
  adopt(chat: string): void;
  /** A fresh new chat starts with commands off. */
  reset(): void;
}

export function chatCommands(storage: Storage | null): ChatCommands {
  let draft = false;
  const read = (): string[] => {
    try {
      const value: unknown = JSON.parse(storage?.getItem(CHAT_COMMANDS_KEY) ?? "[]");
      return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
    } catch { return []; }
  };
  const write = (ids: string[]): void => {
    try { storage?.setItem(CHAT_COMMANDS_KEY, JSON.stringify(ids.slice(-CHAT_COMMANDS_LIMIT))); } catch { void 0; }
  };
  const api: ChatCommands = {
    get: chat => chat === null ? draft : read().includes(chat),
    set(chat, on) {
      if (chat === null) { draft = on; return; }
      const ids = read().filter(id => id !== chat);
      write(on ? [...ids, chat] : ids);
    },
    adopt(chat) {
      const on = draft;
      draft = false;
      if (on) api.set(chat, true);
    },
    reset() { draft = false; },
  };
  return api;
}
