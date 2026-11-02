// Session-only handoff for structure drafts that were already open before the
// structure editor moved from smart instructions into the template workspace.
// Nothing here writes configuration or serializes drafts to browser storage.
export function createPromptDraftTransferBridge() {
  const pending = new Map();
  const receivers = new Set();

  function dispatch(entry) {
    if (entry.operation) return entry.operation;
    entry.operation = (async () => {
      await Promise.resolve();
      try {
        for (const receiver of [...receivers]) {
          if (!receivers.has(receiver)) continue;
          let accepted = false;
          try { accepted = await receiver(entry.payload); } catch { /* Keep the source draft and retry after a receiver changes. */ }
          if (!accepted) continue;
          let released = false;
          try { released = (await entry.acknowledge()) !== false; } catch { /* A source that cannot release still owns its draft. */ }
          if (released && pending.get(entry.payload.id) === entry) pending.delete(entry.payload.id);
          break;
        }
      } finally {
        entry.operation = null;
        if (entry.retryRequested && pending.get(entry.payload.id) === entry) {
          entry.retryRequested = false;
          void dispatch(entry);
        }
      }
    })();
    return entry.operation;
  }

  async function flush() {
    await Promise.all([...pending.values()].map(dispatch));
  }

  function publish(payload, acknowledge = () => true) {
    if (!payload?.id || !payload.draft || payload.draft.id !== payload.id) return false;
    const previous = pending.get(payload.id);
    if (previous && previous.payload.draft === payload.draft && previous.payload.conflict === payload.conflict && previous.payload.error === payload.error) previous.acknowledge = acknowledge;
    else pending.set(payload.id, { payload, acknowledge, operation: null });
    void flush();
    return true;
  }

  function register(receiver) {
    receivers.add(receiver);
    for (const entry of pending.values()) if (entry.operation) entry.retryRequested = true;
    void flush();
    return () => { receivers.delete(receiver); };
  }

  return { publish, register, flush, pending: () => [...pending.values()].map(entry => entry.payload) };
}

const bridge = createPromptDraftTransferBridge();
export const publishPromptDraftTransfer = bridge.publish;
export const registerPromptDraftReceiver = bridge.register;

