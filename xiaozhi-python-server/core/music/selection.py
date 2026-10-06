"""Conservative recording choices for chat; album browsing keeps every entry."""
import re
from core.music.netease import match_text

VARIANT = re.compile(r'\blive\b|现场|演唱会|音乐会|\bconcert\b|\bremix\b|混音|伴奏|卡拉|\bkaraoke\b|\binstrumental\b|\bacoustic\b|不插电|翻唱|\bcover\b|重录|重新录制|重制|\bre[-\s]?record(?:ed|ing)?\b|\bremaster(?:ed)?\b|重新演绎|\bdemo\b|纯音乐|加速|慢速|\bsped\s*up\b|\bslowed\b', re.I)
COMPILATION = re.compile(r'精选|合集|合辑|全纪录|全记录|精华|\bbest\s*of\b|\bgreatest\s*hits\b|\bcollection\b|\bcompilation\b', re.I)


def variant(row):
    return bool(VARIANT.search(row.get('title', '')+' '+row.get('album', '')))


def unrequested_variant(row, title):
    return bool(title and variant(row) and not VARIANT.search(title))


def preference(row):
    compilation = bool(COMPILATION.search(row.get('album', '')+' '+row.get('albumType', '')))
    original_album = row.get('albumType', '').casefold() in ('专辑', 'album', 'studio album')
    published = row.get('publishedAt', 0)
    return (compilation, not original_album, published if isinstance(published, (int,float)) and published>0 else float('inf'))


def recording_choices(rows):
    """Merge ordinary same-title/artist entries only when durations also agree.

    This is metadata matching, not an audio fingerprint. Live/remix/re-recorded
    entries remain separate, and unknown durations never establish equivalence.
    """
    groups = []; seen = set()
    for row in rows:
        if row['id'] in seen: continue
        seen.add(row['id'])
        key = (match_text(row.get('artist', '')), match_text(row.get('title', '')))
        duration = row.get('durationMs', 0)
        group = next((group for group in groups if key[0] and key[1] and group['key']==key
            and not variant(row) and not variant(group['anchor'])
            and duration>0 and group['anchor'].get('durationMs', 0)>0
            and abs(duration-group['anchor']['durationMs'])<=max(3000, group['anchor']['durationMs']*.01)), None)
        if group is None: groups.append(dict(key=key, anchor=row, rows=[row]))
        else: group['rows'].append(row)
    return [min(group['rows'], key=preference) for group in groups]


def original_choice(rows, artist=None, title=None, has_more=False):
    if not title: return None
    # Partial song names and similar song names must not select another track.
    exact = [row for row in rows if match_text(row.get('title', ''))==match_text(title)]
    if artist:
        exact = [row for row in exact if match_text(row.get('artist', ''))==match_text(artist)]
    if not exact or not artist and has_more: return None
    requested_variant = bool(VARIANT.search(title))
    ordinary = exact if requested_variant else [row for row in exact if not variant(row)]
    choices = recording_choices(ordinary)
    return choices[0] if len(choices)==1 else None
