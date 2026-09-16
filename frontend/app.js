let currentMediaData = null;

// Format duration to HH:MM:SS
function formatTime(seconds) {
  const s = Math.floor(seconds);
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

// Clipboard auto-detect
window.addEventListener('focus', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text && text.includes('youtu') && !document.getElementById('urlInput').value) {
      document.getElementById('urlInput').value = text;
      inspectUrl(text);
    }
  } catch (_) {}
});

document.getElementById('pasteBtn').addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    document.getElementById('urlInput').value = text;
    inspectUrl(text);
  } catch (err) {
    alert("Please allow clipboard permissions.");
  }
});

document.getElementById('inspectBtn').addEventListener('click', () => {
  const url = document.getElementById('urlInput').value;
  if (url) inspectUrl(url);
});

async function inspectUrl(url) {
  const spinner = document.getElementById('inspectSpinner');
  spinner.classList.remove('hidden');

  try {
    const res = await fetch('/api/v1/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Failed to inspect link');
    
    currentMediaData = await res.json();
    renderMediaView(currentMediaData);
  } catch (err) {
    alert(err.message);
  } finally {
    spinner.classList.add('hidden');
  }
}

function renderMediaView(data) {
  document.getElementById('mediaCard').classList.remove('hidden');
  document.getElementById('mediaThumbnail').src = data.thumbnail;
  document.getElementById('mediaTitle').innerText = data.title;
  document.getElementById('mediaAuthor').innerText = data.uploader;
  document.getElementById('mediaDuration').innerText = `Duration: ${formatTime(data.duration)}`;

  // Preview Player
  const audioPreview = document.getElementById('audioPreview');
  audioPreview.src = data.preview_audio_url || '';

  // Trimming setup
  const trimToggle = document.getElementById('trimToggle');
  const trimSliders = document.getElementById('trimSliders');
  const startRange = document.getElementById('startRange');
  const endRange = document.getElementById('endRange');
  
  trimToggle.checked = false;
  trimSliders.classList.add('hidden');
  startRange.max = data.duration;
  endRange.max = data.duration;
  startRange.value = 0;
  endRange.value = data.duration;
  updateTrimLabels();

  trimToggle.onchange = () => {
    trimSliders.classList.toggle('hidden', !trimToggle.checked);
  };
  startRange.oninput = () => {
    if (parseInt(startRange.value) >= parseInt(endRange.value)) {
      startRange.value = parseInt(endRange.value) - 1;
    }
    updateTrimLabels();
  };
  endRange.oninput = () => {
    if (parseInt(endRange.value) <= parseInt(startRange.value)) {
      endRange.value = parseInt(startRange.value) + 1;
    }
    updateTrimLabels();
  };

  // Video Options Population
  const videoOptsContainer = document.getElementById('videoOptions');
  videoOptsContainer.innerHTML = '';
  
  // Direct stream <= 720p
  data.direct_video.forEach(v => {
    const btn = document.createElement('button');
    btn.className = "opt-btn px-3 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-bold rounded-lg border border-slate-700";
    btn.innerText = `${v.resolution} (Direct)`;
    btn.onclick = () => window.open(v.direct_url, '_blank');
    videoOptsContainer.appendChild(btn);
  });

  // DASH / Muxed streams
  data.muxed_video.forEach(v => {
    const btn = document.createElement('button');
    btn.className = "opt-btn px-3 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-bold rounded-lg border border-slate-700";
    btn.innerText = `${v.resolution} MP4`;
    btn.onclick = () => triggerJob({
      target_format: "mp4",
      resolution: v.resolution
    });
    videoOptsContainer.appendChild(btn);
  });

  // Audio Buttons Setup
  document.querySelectorAll('.opt-btn[data-type="audio"]').forEach(btn => {
    btn.onclick = () => triggerJob({
      target_format: btn.dataset.format,
      bitrate: btn.dataset.bitrate || '320k'
    });
  });
}

function updateTrimLabels() {
  document.getElementById('lblStart').innerText = formatTime(document.getElementById('startRange').value);
  document.getElementById('lblEnd').innerText = formatTime(document.getElementById('endRange').value);
}

async function triggerJob(options) {
  const trimEnabled = document.getElementById('trimToggle').checked;
  const payload = {
    url: document.getElementById('urlInput').value,
    target_format: options.target_format,
    bitrate: options.bitrate || "320k",
    resolution: options.resolution || null,
    title: currentMediaData.title,
    artist: currentMediaData.uploader,
    thumbnail: currentMediaData.thumbnail,
    trim: {
      enabled: trimEnabled,
      start: trimEnabled ? formatTime(document.getElementById('startRange').value) : null,
      end: trimEnabled ? formatTime(document.getElementById('endRange').value) : null
    }
  };

  const progressContainer = document.getElementById('progressContainer');
  const downloadContainer = document.getElementById('downloadContainer');
  progressContainer.classList.remove('hidden');
  downloadContainer.classList.add('hidden');

  try {
    const res = await fetch('/api/v1/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const { job_id } = await res.json();
    listenToEvents(job_id);
  } catch (err) {
    alert(`Failed to start job: ${err.message}`);
  }
}

function listenToEvents(jobId) {
  const evtSource = new EventSource(`/api/v1/jobs/${jobId}/events`);
  const stage = document.getElementById('progressStage');
  const percent = document.getElementById('progressPercent');
  const bar = document.getElementById('progressBar');
  const downloadContainer = document.getElementById('downloadContainer');
  const finalBtn = document.getElementById('finalDownloadBtn');

  evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    stage.innerText = data.stage;
    percent.innerText = `${data.percent}%`;
    bar.style.width = `${data.percent}%`;

    if (data.stage === "COMPLETE") {
      evtSource.close();
      downloadContainer.classList.remove('hidden');
      finalBtn.href = data.download_url;
      finalBtn.setAttribute('download', '');
    }
  };

  evtSource.onerror = () => {
    evtSource.close();
    stage.innerText = "Connection lost. Checking status...";
  };
}