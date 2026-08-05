import { AlertTriangle, FileVideo2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

function releaseTier(value: string): { label: string; risky: boolean } | null {
  const normalized = value.toLocaleUpperCase();
  if (/\b(?:HD)?CAM(?:RIP)?\b/.test(normalized)) return { label: 'CAM', risky: true };
  if (/\b(?:HD)?TELESYNC\b|\bTS\b/.test(normalized)) return { label: 'TELESYNC', risky: true };
  if (/\bTELECINE\b|\bTC\b/.test(normalized)) return { label: 'TELECINE', risky: true };
  if (/\bWEB[- .]?(?:DL|RIP)\b|\bWEB\b/.test(normalized)) return { label: 'WEB', risky: false };
  if (/\b(?:BLU[- .]?RAY|BDRIP|BRRIP)\b/.test(normalized)) return { label: 'BLU-RAY', risky: false };
  return null;
}

export function GermanRelease({ value, className }: { value?: string | null; className?: string }) {
  if (!value) return null;
  const tier = releaseTier(value);
  return (
    <div className={cn('mt-2 flex min-w-0 items-start gap-2', className)}>
      <FileVideo2 className='mt-0.5 size-3.5 shrink-0 text-muted-foreground' />
      <span className='min-w-0 break-all font-mono text-xs leading-5 text-foreground'>{value}</span>
      {tier && (
        <Badge
          variant={tier.risky ? 'destructive' : 'success'}
          className='shrink-0 gap-1'
          title={tier.risky ? 'Low-quality cinema recording. You may want to wait for a WEB or Blu-ray release.' : undefined}
        >
          {tier.risky && <AlertTriangle className='size-3' />}
          {tier.label}
        </Badge>
      )}
    </div>
  );
}
