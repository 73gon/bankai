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
        if (followId) ws.send(JSON.stringify({ follow: followId }));
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (!closed) reconnect = window.setTimeout(connect, 3000);
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

  // REST polling fallback — always runs so the queue works even when the
  // WebSocket can't connect (e.g. proxy without WS upgrade).
  useEffect(() => {
    let active = true;
    async function poll() {
      if (wsConnected) return; // WS already feeding jobs
      try {
        const r = await api.queue();
        if (active) {
          setJobs(r.jobs);
          setPollOk(true);
        }
      } catch {
        if (active) setPollOk(false);
      }
    }
    poll();
    const id = window.setInterval(poll, 2500);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [wsConnected]);

  useEffect(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ follow: followId || '' }));
    }
    if (!followId) setLogTail('');
  }, [followId]);

  return { jobs, logTail, connected: wsConnected || pollOk, live: wsConnected };
}
