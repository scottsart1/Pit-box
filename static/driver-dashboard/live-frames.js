// Each socket has its own strategy cache. A reconnect starts with a full frame.
function receiveLiveFrames(socket, renderFrame, wantsTraces = () => false, onError = () => {}) {
  let strategy = null;
  let revision = null;
  let session = null;
  return event => {
    try {
      const frame = JSON.parse(event.data);
      if (frame.strategy?.unchanged === true) {
        // Keep speed, flags and timing live while requesting a missed plan.
        if (session !== frame.session_identity) strategy = null;
        if (strategy === null || revision !== frame.strategy.revision) revision = null;
        frame.strategy = strategy ?? {};
      } else {
        session = frame.session_identity;
        strategy = frame.strategy ?? null;
        revision = strategy !== null && Number.isInteger(frame.strategy_revision)
          ? frame.strategy_revision : null;
      }
      renderFrame(frame);
    } catch (error) {
      strategy = null;
      revision = null;
      onError(error);
    } finally {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ack: 1, strategy: revision, traces: wantsTraces()}));
      }
    }
  };
}
