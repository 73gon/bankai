import { useEffect, useState } from 'react';
import { Download, Replace } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { api, type ReplacementPlan } from '@/lib/api';

// A season pack ("Show - S01") names no last episode: every one it has.
const SEASON_PACK_LAST = 999;

export const pad = (n: number) => String(n).padStart(2, '0');

/** "episode 05", "episodes 01–12", or "the whole season" for a season pack. */
export function episodesLabel([first, last]: [number, number]) {
  if (last >= SEASON_PACK_LAST) return 'the whole season';
  return first === last ? `episode ${pad(first)}` : `episodes ${pad(first)}–${pad(last)}`;
}

export function replaceLabel(episodes: [number, number]) {
  const [first, last] = episodes;
  if (last >= SEASON_PACK_LAST) return 'Replace whole season';
  return first === last ? `Replace episode ${pad(first)}` : `Replace season ${pad(first)}–${pad(last)}`;
}

/** The release a card would take: one Erai-raws lists, or a batch held for the card. */
export interface ReplaceCandidate {
  info_hash: string;
  name: string;
  episodes: [number, number];
}

/**
 * The confirm step for taking a release in place of what a review card holds.
 *
 * Shows what it would change before anything is queued. On confirm the dialog
 * closes at once and the queueing runs in the background: ``onStart`` and
 * ``onSettled`` bracket it, ``onQueued`` follows a success.
 */
export function ReplaceDialog({
  cardKey,
  label,
  candidate,
  verb = 'Replace',
  onClose,
  onStart,
  onSettled,
  onQueued,
}: {
  cardKey: string;
  label: string;
  candidate: ReplaceCandidate | null;
  verb?: 'Replace' | 'Download batch';
  onClose: () => void;
  onStart?: (infoHash: string) => void;
  onSettled?: (infoHash: string) => void;
  onQueued?: () => void;
}) {
  const [plan, setPlan] = useState<ReplacementPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  useEffect(() => {
    if (!candidate) return;
    let current = true;
    setPlan(null);
    setPlanError(null);
    api
      .replacementPlan(cardKey, candidate.info_hash)
      .then((answer) => current && setPlan(answer))
      .catch((error: any) => current && setPlanError(error.message));
    return () => {
      current = false;
    };
  }, [cardKey, candidate]);

  async function confirm() {
    if (!candidate) return;
    const row = candidate;
    onClose();
    onStart?.(row.info_hash);
    try {
      const result = await api.replaceFromErai(cardKey, row.info_hash, label);
      toast.success(`Queued ${result.queued}`);
      onQueued?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      onSettled?.(row.info_hash);
    }
  }

  const Icon = verb === 'Replace' ? Replace : Download;
  const title = !candidate
    ? verb
    : verb === 'Replace'
      ? replaceLabel(candidate.episodes)
      : `Download batch: ${episodesLabel(candidate.episodes)}`;

  return (
    <Dialog open={candidate !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className='max-w-xl'>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className='break-words'>{candidate?.name}</DialogDescription>
        </DialogHeader>
        {planError ? (
          <p className='rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive'>{planError}</p>
        ) : !plan ? (
          <p className='text-sm text-muted-foreground'>Working out what it would change…</p>
        ) : (
          <ul className='flex flex-col gap-1.5 text-sm'>
            <li>
              Downloads {episodesLabel([plan.first, plan.last])} of{' '}
              <span className='font-medium text-foreground'>{plan.anidb_title}</span>
              {plan.german ? ', with German subtitles as listed for it.' : '. No German subtitles are listed for it.'}
            </li>
            <li>{plan.held_count ? `Replaces ${plan.held_count} held release${plan.held_count === 1 ? '' : 's'} in review.` : 'No other held release of this card falls in its episodes.'}</li>
            <li>
              {plan.file_count
                ? `Replaces ${plan.file_count} file${plan.file_count === 1 ? '' : 's'} already in the library, removed once the new ones are published.`
                : 'Nothing of these episodes is in the library yet.'}
            </li>
            {plan.german_dubs_kept.length > 0 && (
              <li className='text-transfer'>
                Keeps {plan.german_dubs_kept.length === 1 ? 'episode' : 'episodes'} {plan.german_dubs_kept.map(pad).join(', ')}: German dub, never replaced.
              </li>
            )}
            <li className='text-muted-foreground'>If publishing fails for good, the held releases go back to review.</li>
          </ul>
        )}
        <DialogFooter>
          <Button variant='secondary' onClick={onClose}>Cancel</Button>
          <Button onClick={() => void confirm()} disabled={!plan}>
            <Icon data-icon='inline-start' /> {verb}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
