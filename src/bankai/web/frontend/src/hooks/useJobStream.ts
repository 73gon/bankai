import { useEffect, useRef, useState } from "react";
import type { Job } from "@/lib/api";

interface QueueMessage {
  type: string;
  jobs?: Job[];
  log?: { id: string; tail: string };
}

export function useJobStream(followId?: string | null) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [logTail, setLogTail] = useState<string>("");
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    wsRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      if (followId) ws.send(JSON.stringify({ follow: followId }));
    };
    ws.onclose = () => setConnected(false);
    ws.onmessage = (ev) => {
      try {
        const msg: QueueMessage = JSON.parse(ev.data);
        if (msg.jobs) setJobs(msg.jobs);
        if (msg.log) setLogTail(msg.log.tail);
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ follow: followId || "" }));
    }
    if (!followId) setLogTail("");
  }, [followId]);

  return { jobs, logTail, connected };
}
