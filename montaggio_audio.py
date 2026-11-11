"""Conservative PCM edge trimming and chapter review metrics."""
from array import array
import math
import sys

VERSION = 'edges-1'
EDGE_GUARD_MS = 45
EDGE_THRESHOLD = 184  # Approximately -45 dBFS.


def mount_pcm(frames, channels, width, rate, pause_ms, *, following=False):
    """Trim only the outside of a clip and return PCM plus measured margins."""
    frame_size = channels * width
    frame_count = len(frames) // frame_size
    pause = round(rate * pause_ms / 1000)
    details = {'trim_head': 0, 'trim_tail': 0, 'pause_frames': pause}
    if width != 2 or not frame_count:
        return frames, details

    samples = array('h', frames)
    if sys.byteorder != 'little':
        samples.byteswap()
    first = next((i for i, value in enumerate(samples) if abs(value) >= EDGE_THRESHOLD), None)
    if first is None:
        details['review'] = ['weak_or_silent']
        return frames, details
    end = len(samples) - next(i for i, value in enumerate(reversed(samples))
                              if abs(value) >= EDGE_THRESHOLD)
    start_frame = first // channels
    end_frame = (end + channels - 1) // channels
    guard = round(rate * EDGE_GUARD_MS / 1000)
    left = max(0, start_frame - guard)
    right = min(frame_count, end_frame + guard)
    head, tail = start_frame - left, right - end_frame
    next_head = guard if following else 0
    details.update(trim_head=left, trim_tail=frame_count - right, head_frames=head,
                   tail_frames=tail, pause_frames=max(0, pause - tail - next_head))
    peak = max(abs(value) for value in samples)
    active = memoryview(samples)[start_frame * channels:end_frame * channels]
    power = sum(sample * sample for sample in active) / len(active)
    details['rms_dbfs'] = round(10 * math.log10(power / 32768 ** 2), 2)
    details['peak_dbfs'] = round(20 * math.log10(peak / 32768), 2)
    details['review'] = [flag for flag, present in (
        ('peak', details['peak_dbfs'] >= -1),
        ('long_clip', (end_frame - start_frame) / rate > 20),
        ('long_edge', max(start_frame, frame_count - end_frame) / rate >= .75),
    ) if present]
    return frames[left * frame_size:right * frame_size], details


def flag_level_jump(previous, current):
    before, after = previous.get('rms_dbfs'), current.get('rms_dbfs')
    if before is None or after is None or previous['id'] + 1 != current['id']:
        return
    change = round(after - before, 2)
    current['level_change_db'] = change
    if abs(change) >= 5:
        flags = current.setdefault('review', [])
        if 'level_jump' not in flags:
            flags.append('level_jump')
