import { create } from "zustand";
import type { RunEvent } from "./types";

type ArenaState = {
  demoMode: boolean;
  events: RunEvent[];
  reconnecting: boolean;
  setDemoMode: (value: boolean) => void;
  hydrateEvents: (events: RunEvent[]) => void;
  appendEvent: (event: RunEvent) => void;
  setReconnecting: (value: boolean) => void;
};

const initialDemo = sessionStorage.getItem("arena-demo") === "1";

export const useArenaStore = create<ArenaState>((set) => ({
  demoMode: initialDemo,
  events: [],
  reconnecting: false,
  setDemoMode: (value) => {
    sessionStorage.setItem("arena-demo", value ? "1" : "0");
    set({ demoMode: value });
  },
  hydrateEvents: (events) => set({ events: [...events].sort((a, b) => a.seq - b.seq) }),
  appendEvent: (event) => set((state) => state.events.some((item) => item.seq === event.seq) ? state : { events: [...state.events, event] }),
  setReconnecting: (value) => set({ reconnecting: value }),
}));
