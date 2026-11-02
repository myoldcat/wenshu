// A GET can finish after a save or lock request. A clean draft at that point
// still must not be replaced with the older response, including its run status.
export function canAcceptWritingSnapshot(incoming, current, baseline, observed) {
  if (!incoming?.id || incoming.id !== current?.id || !Number.isSafeInteger(incoming.version)) return false;
  const versions = [current, baseline, observed]
    .filter(value => value?.id === incoming.id && Number.isSafeInteger(value.version))
    .map(value => value.version);
  return incoming.version >= Math.max(0, ...versions);
}
