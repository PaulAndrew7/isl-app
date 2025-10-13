document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('caption-form');
    const urlInput = document.getElementById('youtube-url');
    const submitBtn = document.getElementById('submit-btn');
    const statusContainer = document.getElementById('status-container');
    const statusMessage = document.getElementById('status-message');
    const progressBar = document.getElementById('progress-bar');
    const resultContainer = document.getElementById('result-container');
    const resultMessage = document.getElementById('result-message');
    const downloadLink = document.getElementById('download-link');
    
    let sessionId = null;
    
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const youtubeUrl = urlInput.value.trim();
        if (!youtubeUrl) {
            showError('Please enter a YouTube URL');
            return;
        }
        
        // Reset UI
        resetUI();
        
        // Show status container
        statusContainer.classList.remove('hidden');
        statusMessage.textContent = 'Checking for manual captions...';
        progressBar.style.width = '10%';
        
        // Disable form
        submitBtn.disabled = true;
        
        // Process the video
        processVideo(youtubeUrl);
    });
    
    function processVideo(url) {
        const formData = new FormData();
        formData.append('url', url);
        
        fetch('/process', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'error') {
                showError(data.message);
                return;
            }
            
            sessionId = data.session_id;
            
            if (data.status === 'success') {
                // Manual captions found
                progressBar.style.width = '100%';
                statusMessage.textContent = data.message;
                
                // Show download link
                showDownloadLink(data.file_path);
            } else if (data.status === 'info') {
                // Need to download audio and transcribe
                progressBar.style.width = '30%';
                statusMessage.textContent = data.message;
                
                // Download audio
                downloadAudio(url, sessionId);
            }
        })
        .catch(error => {
            showError('Error: ' + error.message);
        });
    }
    
    function downloadAudio(url, sessionId) {
        const formData = new FormData();
        formData.append('url', url);
        formData.append('session_id', sessionId);
        
        fetch('/download-audio', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'error') {
                showError(data.message);
                return;
            }
            
            // Audio downloaded, start transcription
            progressBar.style.width = '60%';
            statusMessage.textContent = data.message;
            
            // Transcribe audio
            transcribeAudio(data.audio_path, sessionId);
        })
        .catch(error => {
            showError('Error: ' + error.message);
        });
    }
    
    function transcribeAudio(audioPath, sessionId) {
        const formData = new FormData();
        formData.append('audio_path', audioPath);
        formData.append('session_id', sessionId);
        
        fetch('/transcribe', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'error') {
                showError(data.message);
                return;
            }
            
            // Transcription complete
            progressBar.style.width = '100%';
            statusMessage.textContent = data.message;
            
            // Show download link
            showDownloadLink(data.file_path);
        })
        .catch(error => {
            showError('Error: ' + error.message);
        });
    }
    
    function showDownloadLink(filePath) {
        // Normalize Windows backslashes to forward slashes for URL building
        const normalized = filePath.replace(/\\/g, '/');
        const fileName = normalized.split('/').pop();
        
        resultContainer.classList.remove('hidden');
        resultMessage.textContent = 'Captions ready for download!';
        resultMessage.className = 'success';
        
        downloadLink.href = `/download/${sessionId}/${encodeURIComponent(fileName)}`;
        downloadLink.textContent = `Download ${fileName}`;
        downloadLink.classList.remove('hidden');
        
        // Clean up temporary files when the page is closed
        window.addEventListener('beforeunload', function() {
            cleanupFiles();
        });
    }
    
    function cleanupFiles() {
        if (sessionId) {
            const formData = new FormData();
            formData.append('session_id', sessionId);
            
            fetch('/cleanup', {
                method: 'POST',
                body: formData
            });
        }
    }
    
    function showError(message) {
        statusContainer.classList.add('hidden');
        resultContainer.classList.remove('hidden');
        resultMessage.textContent = message;
        resultMessage.className = 'error';
        downloadLink.classList.add('hidden');
        submitBtn.disabled = false;
    }
    
    function resetUI() {
        statusContainer.classList.add('hidden');
        resultContainer.classList.add('hidden');
        downloadLink.classList.add('hidden');
        progressBar.style.width = '0%';
    }
});