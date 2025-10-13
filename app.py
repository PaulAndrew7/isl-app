import os
import re
import uuid
import shutil
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

if __name__ == '__main__':
    app.run(debug=True)