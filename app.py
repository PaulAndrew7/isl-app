import os
import re
import uuid
import shutil
import string
import json
from collections import Counter
from flask import Flask, render_template, request, jsonify, send_file
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import yt_dlp
from faster_whisper import WhisperModel

app = Flask(__name__)

# Create temp directory if it doesn't exist
os.makedirs('temp', exist_ok=True)

# Clean up any leftover temp sessions on startup
def _cleanup_temp_root():
    try:
        for entry in os.listdir('temp'):
            entry_path = os.path.join('temp', entry)
            if os.path.isdir(entry_path):
                shutil.rmtree(entry_path, ignore_errors=True)
            elif os.path.isfile(entry_path):
                try:
                    os.remove(entry_path)
                except Exception:
                    pass
    except Exception:
        # Best-effort cleanup; ignore failures
        pass

_cleanup_temp_root()

def sanitize_filename(name):
    """Sanitize a string to be a safe filename across OSes."""
    # Remove characters not allowed in Windows filenames and strip whitespace
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    name = name.strip()
    # Collapse spaces and dots at ends
    name = name.strip(' .')
    # Replace remaining runs of whitespace with single space
    name = re.sub(r'\s+', ' ', name)
    return name or 'subtitle'

def extract_video_id(url):
    """Extract the YouTube video ID from a URL."""
    # Regular expression to match YouTube video IDs
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/|youtube\.com\/watch\?.*v=)([^&\n?#]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def format_time(seconds):
    """Convert seconds to SRT time format (HH:MM:SS,mmm)."""
    hours = int(seconds / 3600)
    minutes = int((seconds % 3600) / 60)
    seconds = seconds % 60
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{int(seconds):02d},{milliseconds:03d}"

def transcript_to_srt(transcript):
    """Convert YouTube transcript format to SRT format."""
    srt_content = ""
    for i, segment in enumerate(transcript, 1):
        start_time = format_time(segment['start'])
        # Calculate end time from start time and duration
        end_time = format_time(segment['start'] + segment.get('duration', 0))
        text = segment['text']
        
        srt_content += f"{i}\n{start_time} --> {end_time}\n{text}\n\n"
    
    return srt_content

def whisper_segments_to_srt(segments):
    """Convert Whisper segments to SRT format."""
    srt_content = ""
    for i, segment in enumerate(segments, 1):
        start_time = format_time(segment.start)
        end_time = format_time(segment.end)
        text = segment.text.strip()
        
        srt_content += f"{i}\n{start_time} --> {end_time}\n{text}\n\n"
    
    return srt_content

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_video():
    try:
        url = request.form.get('url')
        if not url:
            return jsonify({'status': 'error', 'message': 'No URL provided'}), 400
        
        video_id = extract_video_id(url)
        if not video_id:
            return jsonify({'status': 'error', 'message': 'Invalid YouTube URL'}), 400
        
        # Create a unique session ID for this request
        session_id = str(uuid.uuid4())
        temp_dir = os.path.join('temp', session_id)
        os.makedirs(temp_dir, exist_ok=True)

        # Get video title for nicer filename
        safe_title = video_id
        try:
            canonical_url = f'https://www.youtube.com/watch?v={video_id}'
            with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
                info = ydl.extract_info(canonical_url, download=False)
                title = info.get('title') or video_id
                safe_title = sanitize_filename(title)
        except Exception:
            # Fallback to video_id if title cannot be fetched
            safe_title = video_id

        srt_path = os.path.join(temp_dir, f"{safe_title}.srt")
        
        # Try to get captions first
        try:
            # Fetch transcript using updated youtube_transcript_api (v1.2+)
            api = YouTubeTranscriptApi()
            transcript_obj = api.fetch(video_id)
            transcript = transcript_obj.to_raw_data()
            
            # If we got here, we have captions (either manual or auto-generated)
            # Convert to SRT format
            srt_content = transcript_to_srt(transcript)
            
            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write(srt_content)
            
            return jsonify({
                'status': 'success', 
                'message': 'Captions found and converted to SRT',
                'file_path': srt_path,
                'session_id': session_id
            })
        except (TranscriptsDisabled, NoTranscriptFound):
            # No captions available, proceed to download and transcribe
            return jsonify({
                'status': 'info', 
                'message': 'No captions available. Downloading audio for transcription...',
                'session_id': session_id
            })
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error processing request: {str(e)}'}), 500

@app.route('/download-audio', methods=['POST'])
def download_audio():
    try:
        url = request.form.get('url')
        session_id = request.form.get('session_id')
        
        if not url or not session_id:
            return jsonify({'status': 'error', 'message': 'Missing URL or session ID'}), 400
        
        video_id = extract_video_id(url)
        if not video_id:
            return jsonify({'status': 'error', 'message': 'Invalid YouTube URL'}), 400
        
        temp_dir = os.path.join('temp', session_id)
        audio_path = os.path.join(temp_dir, f"{video_id}.mp3")
        
        # Download audio using yt-dlp
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': audio_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'noplaylist': True,
            'ignoreerrors': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            canonical_url = f'https://www.youtube.com/watch?v={video_id}'
            ydl.download([canonical_url])
        
        return jsonify({
            'status': 'success', 
            'message': 'Audio downloaded successfully. Starting transcription...',
            'audio_path': audio_path,
            'session_id': session_id
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error downloading audio: {str(e)}'}), 500

@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    try:
        audio_path = request.form.get('audio_path')
        session_id = request.form.get('session_id')
        
        if not audio_path or not session_id:
            return jsonify({'status': 'error', 'message': 'Missing audio path or session ID'}), 400
        
        if not os.path.exists(audio_path):
            return jsonify({'status': 'error', 'message': 'Audio file not found'}), 404
        
        # Load the Whisper model
        model = WhisperModel("small", device="cpu", compute_type="int8")
        
        # Transcribe the audio
        segments, _ = model.transcribe(audio_path, beam_size=5)
        
        # Convert segments to SRT format
        srt_content = whisper_segments_to_srt(segments)
        
        # Save the SRT file
        video_id = os.path.basename(audio_path).split('.')[0]
        srt_path = os.path.join('temp', session_id, f"{video_id}.srt")
        
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        
        return jsonify({
            'status': 'success', 
            'message': 'Transcription completed successfully',
            'file_path': srt_path,
            'session_id': session_id
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error transcribing audio: {str(e)}'}), 500

@app.route('/download/<session_id>/<path:filename>')
def download_file(session_id, filename):
    try:
        file_path = os.path.join('temp', session_id, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'status': 'error', 'message': 'File not found'}), 404
        
        return send_file(file_path, as_attachment=True, download_name=filename)
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error downloading file: {str(e)}'}), 500

@app.route('/cleanup', methods=['POST'])
def cleanup():
    try:
        session_id = request.form.get('session_id')
        
        if not session_id:
            return jsonify({'status': 'error', 'message': 'Missing session ID'}), 400
        
        temp_dir = os.path.join('temp', session_id)
        
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        
        return jsonify({'status': 'success', 'message': 'Cleanup completed successfully'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error during cleanup: {str(e)}'}), 500

# ---------- STEP: Spoken -> Written (formal) converter ----------

# Basic contraction map (extend as needed)
_CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "he's": "he is", "she's": "she is", "it's": "it is", "that's": "that is",
    "there's": "there is", "what's": "what is", "who's": "who is", "where's": "where is",
    "when's": "when is", "how's": "how is",
    "i've": "i have", "we've": "we have", "they've": "they have", "you've": "you have",
    "could've": "could have", "should've": "should have", "would've": "would have",
    "i'd": "i would", "you'd": "you would", "he'd": "he would", "she'd": "she would",
    "we'd": "we would", "they'd": "they would",
    "i'll": "i will", "you'll": "you will", "he'll": "he will", "she'll": "she will",
    "we'll": "we will", "they'll": "they will",
    "can't": "cannot", "won't": "will not", "don't": "do not", "doesn't": "does not",
    "didn't": "did not", "isn't": "is not", "aren't": "are not", "wasn't": "was not",
    "weren't": "were not", "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
    "shouldn't": "should not", "wouldn't": "would not", "couldn't": "could not",
    "mustn't": "must not", "ain't": "is not",
    "gonna": "going to", "wanna": "want to", "gotta": "have to", "kinda": "kind of",
    "sorta": "sort of", "lemme": "let me", "gimme": "give me", "outta": "out of",
    "lotta": "a lot of", "dunno": "do not know", "cuz": "because", "’re": " are", "’s": " is"
}

# Common fillers (remove)
_FILLERS = {
    "uh", "um", "erm", "hmm", "ah", "uhh", "umm", "like", "you know", "i mean",
    "sort of", "kind of", "kinda", "sorta", "basically", "literally", "actually",
    "okay", "ok", "so", "well", "right", "yeah", "you see"
}

# Parenthetical/noise patterns to remove, e.g., (laughs), [music], <noise>
_NOISE_PATTERNS = [
    r"\[(?:[^\]]+)\]", r"\((?:[^)]+)\)", r"\<(?:[^>]+)\>"
]

def _remove_noise(text: str) -> str:
    t = text
    for pat in _NOISE_PATTERNS:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)
    return t

def _expand_contractions(text: str) -> str:
    def repl(m):
        w = m.group(0)
        low = w.lower()
        return _CONTRACTIONS.get(low, w)
    pattern = r"\b(" + "|".join(map(re.escape, sorted(_CONTRACTIONS.keys(), key=len, reverse=True))) + r")\b"
    return re.sub(pattern, repl, text, flags=re.IGNORECASE)

def _remove_fillers(text: str) -> str:
    t = text
    for phrase in sorted(_FILLERS, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(phrase)}\b", " ", t, flags=re.IGNORECASE)
    return t

def _dedupe_repeated_words(text: str) -> str:
    return re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)

def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def _smart_punctuate(sentence: str) -> str:
    s = sentence.strip()
    if not s:
        return s
    s = s[0].upper() + s[1:] if s[0].isalpha() else s
    if s[-1] not in ".?!":
        if re.search(r"\b(who|what|when|where|why|how)\b.*\b(is|are|do|did|can|could|will|would|should)\b", s, re.IGNORECASE):
            s += "?"
        else:
            s += "."
    return s

def spoken_to_written(text: str) -> str:
    t = text
    t = _remove_noise(t)
    t = _expand_contractions(t)
    t = _remove_fillers(t)
    t = _dedupe_repeated_words(t)
    t = _normalize_spaces(t)

    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", t) if p.strip()]
    if not parts:
        return t

    cleaned = []
    for p in parts:
        cleaned.append(_smart_punctuate(p))
    out = " ".join(cleaned)
    return _normalize_spaces(out)

# --- SRT parsing/writing helpers for formalize ---

_SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n"
    r"([\s\S]*?)(?:\n{2,}|\Z)",
    flags=re.UNICODE
)

def parse_srt(content: str):
    blocks = []
    for m in _SRT_BLOCK_RE.finditer(content):
        idx = int(m.group(1))
        start = m.group(2)
        end = m.group(3)
        text_lines = m.group(4).splitlines()
        text = " ".join(line.strip() for line in text_lines).strip()
        blocks.append({"index": idx, "start": start, "end": end, "text": text})
    return blocks

def write_srt(blocks) -> str:
    out = []
    for i, b in enumerate(blocks, 1):
        out.append(str(i))
        out.append(f"{b['start']} --> {b['end']}")
        text = b["text"]
        lines = []
        while len(text) > 42:
            cut = text.rfind(" ", 0, 42)
            if cut == -1:
                cut = 42
            lines.append(text[:cut])
            text = text[cut:].lstrip()
        if text:
            lines.append(text)
        out.extend(lines)
        out.append("")
    return "\n".join(out).rstrip() + "\n"

@app.route('/formalize', methods=['POST'])
def formalize_srt():
    """
    POST form-data:
      - session_id (required)
      - file_path OR filename (optional; if omitted, picks first .srt in session folder)
    Produces: *.formal.srt in same session folder and returns its path.
    """
    try:
        session_id = request.form.get('session_id')
        file_path = request.form.get('file_path')
        filename = request.form.get('filename')

        if not session_id:
            return jsonify({'status': 'error', 'message': 'Missing session ID'}), 400

        temp_dir = os.path.join('temp', session_id)
        if not os.path.isdir(temp_dir):
            return jsonify({'status': 'error', 'message': 'Session not found'}), 404

        if file_path:
            srt_in = file_path
        elif filename:
            srt_in = os.path.join(temp_dir, filename)
        else:
            candidates = [f for f in os.listdir(temp_dir) if f.lower().endswith(".srt")]
            if not candidates:
                return jsonify({'status': 'error', 'message': 'No SRT file found in session'}), 404
            srt_in = os.path.join(temp_dir, candidates[0])

        if not os.path.exists(srt_in):
            return jsonify({'status': 'error', 'message': 'SRT file not found'}), 404

        with open(srt_in, 'r', encoding='utf-8', errors='ignore') as f:
            raw = f.read()

        blocks = parse_srt(raw)
        if not blocks:
            return jsonify({'status': 'error', 'message': 'SRT could not be parsed'}), 400

        for b in blocks:
            b['text'] = spoken_to_written(b['text'])

        base, ext = os.path.splitext(os.path.basename(srt_in))
        srt_out = os.path.join(temp_dir, f"{base}.formal.srt")
        with open(srt_out, 'w', encoding='utf-8') as f:
            f.write(write_srt(blocks))

        return jsonify({
            'status': 'success',
            'message': 'Formalization complete',
            'input_file': srt_in,
            'file_path': srt_out,
            'session_id': session_id
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error formalizing SRT: {str(e)}'}), 500

# ---------- STEP: spaCy extractor for ISL sign words ----------

# lazy-load spaCy once
_nlp = None
def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            # helpful error if model is missing
            raise RuntimeError("spaCy model 'en_core_web_sm' not found. Install via: python -m spacy download en_core_web_sm")
    return _nlp

# Hard-coded wordlist = words you have avatar videos for (lowercase, lemmas)
with open("static/isl_sign_words.txt", "r", encoding="utf-8") as f:
    lines = f.readlines()
    ISL_SIGN_WORDS = {line.strip().lower() for line in lines}
# ISL_SIGN_WORDS = {
#     "hello","thanks","thank","sorry","please","name","what","where","who","how","why","yes","no",
#     "go","come","stop","start","eat","drink","sleep","read","write","learn","school","college",
#     "car","bus","train","bike","road","break","help","call","see","watch","want","need",
#     "time","day","night","today","yesterday","tomorrow","morning","evening",
#     "good","bad","big","small","more","less","arrive","baby","be",
#     # add your real list here
# }

def _read_srt_for_session(session_id: str, file_path: str=None, filename: str=None) -> tuple[str, str]:
    """Return (srt_path, raw_text) choosing .formal.srt first if not specified."""
    temp_dir = os.path.join('temp', session_id)
    if not os.path.isdir(temp_dir):
        raise FileNotFoundError("Session not found")

    if file_path:
        srt_in = file_path
    elif filename:
        srt_in = os.path.join(temp_dir, filename)
    else:
        # prefer .formal.srt
        candidates = [f for f in os.listdir(temp_dir) if f.lower().endswith(".formal.srt")]
        if not candidates:
            candidates = [f for f in os.listdir(temp_dir) if f.lower().endswith(".srt")]
        if not candidates:
            raise FileNotFoundError("No SRT file found in session")
        srt_in = os.path.join(temp_dir, candidates[0])

    if not os.path.exists(srt_in):
        raise FileNotFoundError("SRT file not found")

    with open(srt_in, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    # Use your existing parse_srt() to extract caption texts
    blocks = parse_srt(raw)
    text = " ".join(b["text"] for b in blocks)
    return srt_in, text

# --- replace ONLY the isl_extract() in app.py with this ---
@app.route('/isl-extract', methods=['POST'])
def isl_extract():
    """
    POST form-data:
      - session_id (required)
      - file_path OR filename (optional)
    Returns JSON with:
      - unique_matches (existing): lemmas that intersect ISL_SIGN_WORDS
      - counts (existing): counts for those lemmas
      - affected_lemmas (new): lemmas where spaCy changed the surface form
      - affected_present / affected_absent (new): affected lemmas split by ISL list presence
    """
    try:
        session_id = request.form.get('session_id')
        file_path = request.form.get('file_path')
        filename  = request.form.get('filename')

        if not session_id:
            return jsonify({'status': 'error', 'message': 'Missing session ID'}), 400

        srt_in, text = _read_srt_for_session(session_id, file_path, filename)

        nlp = _get_nlp()
        doc = nlp(text)

        # all lemmas (alpha only)
        lemmas = [t.lemma_.lower() for t in doc if t.is_alpha]
        counts = Counter(lemmas)

        # existing: intersect all lemmas with ISL list
        matched = sorted(w for w in counts.keys() if w in ISL_SIGN_WORDS)

        # NEW: find tokens "affected by spaCy" (lemma != surface)
        affected_map = {}  # lemma -> set of original forms
        affected_counts = Counter()  # lemma -> count across tokens
        for t in doc:
            if not t.is_alpha:
                continue
            orig = t.text.lower()
            lem  = t.lemma_.lower()
            if lem != orig:
                affected_counts[lem] += 1
                affected_map.setdefault(lem, set()).add(orig)

        # Build array of affected lemmas with originals + counts
        affected_lemmas = [
            {
                "lemma": lem,
                "originals": sorted(list(affected_map[lem])),
                "count": affected_counts[lem]
            }
            for lem in sorted(affected_map.keys())
        ]

        # Split affected lemmas by presence in ISL_SIGN_WORDS
        affected_present = [
            {"lemma": lem, "count": affected_counts[lem]}
            for lem in sorted(affected_map.keys())
            if lem in ISL_SIGN_WORDS
        ]
        affected_absent = [
            {"lemma": lem, "count": affected_counts[lem]}
            for lem in sorted(affected_map.keys())
            if lem not in ISL_SIGN_WORDS
        ]

        # Persist (optional) for debugging
        out_path = os.path.join('temp', session_id, 'isl_matches.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({
                'source_srt': os.path.basename(srt_in),
                'unique_matches': matched,
                'counts': {w: counts[w] for w in matched},
                'affected_lemmas': affected_lemmas,
                'affected_present': affected_present,
                'affected_absent': affected_absent
            }, f, indent=2, ensure_ascii=False)

        return jsonify({
            'status': 'success',
            'session_id': session_id,
            'source_srt': os.path.basename(srt_in),
            'unique_matches': matched,
            'counts': {w: counts[w] for w in matched},
            'affected_lemmas': affected_lemmas,
            'affected_present': affected_present,
            'affected_absent': affected_absent,
            'download_url': f"/download/{session_id}/isl_matches.json"
        })
    except FileNotFoundError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 404
    except RuntimeError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error extracting ISL words: {str(e)}'}), 500


if __name__ == '__main__':
    app.run(debug=True)