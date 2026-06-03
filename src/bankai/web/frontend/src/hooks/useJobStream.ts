import { useEffect, useRef, useState } from 'react';
import { api, type Job } from '@/lib/api';

interface QueueMessage {
  type: string;
  jobs?: Job[];
  log?: { id: string; tail: string };
}

export function useJobStream(followId?: string | null) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [logTail, setLogTail] = useState<string>('');
  const [wsConnected, setWsConnected] = useState(false);
  const [pollOk, setPollOk] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const wsConnectedRef = useRef(false);

  // Live WebSocket stream (preferred).
  useEffect(() => {
    let closed = false;
    let reconnect: number | undefined;

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
      wsRef.current = ws;
      ws.onopen = () => {
        setWsConnected(true);
        wsConnectedRef.current = true;
        if (followId) ws.send(JSON.stringify({ follow: followId }));
      };
      ws.onclose = () => {
        setWsConnected(false);
        wsConnectedRef.current = false;
        // Reconnect quickly; the polling heartbeat keeps the queue live
        // in the meantime so the UI never flips to "offline" on a blip.
        if (!closed) reconnect = window.setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (ev) => {
        try {
          const msg: QueueMessage = JSON.parse(ev.data);
          if (msg.jobs) setJobs(msg.jobs);
          if (msg.log) setLogTail(msg.log.tail);
        } catch {
          /* ignore */
        }
      };
    }
    connect();
    return () => {
      closed = true;
      window.clearTimeout(reconnect);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // REST polling heartbeat — always runs (even while the WebSocket is up)
  // so a momentary WS drop never shows "offline" and the queue stays fresh
  // behind proxies that can't upgrade WebSocket connections.
  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const r = await api.queue();
        if (!active) return;
        setPollOk(true);
        if (!wsConnectedRef.current) setJobs(r.jobs); // avoid double updates
      } catch {
        if (active) setPollOk(false);
      }
    }
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ follow: followId || '' }));
    }
    if (!followId) setLogTail('');
  }, [followId]);

  return { jobs, logTail, connected: wsConnected || pollOk, live: wsConnected };
}
