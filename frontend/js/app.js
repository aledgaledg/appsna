const form      = document.getElementById('uploadForm');
const fileInput = document.getElementById('fileInput');
const msgArea   = document.getElementById('messageArea');
const loader    = document.getElementById('loadingIndicator');

function clearMessages() { msgArea.innerHTML = ''; }
function showMessage(text, type='info') {
  const div = document.createElement('div');
  div.className = `message ${type}`;
  div.textContent = text;
  msgArea.append(div);
}

form.addEventListener('submit', async function(event) {
    event.preventDefault();
    // Show the loading indicator
    loader.classList.remove('hidden');
    clearMessages();
    const files = Array.from(fileInput.files)
        .filter(f => f.name.toLowerCase().endsWith('.pdf'));
    
    if (!files.length) {
        loader.classList.add('hidden');
        return showMessage('Seleziona almeno un PDF.', 'warning');
    }
    
    const fd = new FormData();
    files.forEach(f => fd.append('pdf_files', f));
    
    try {
        const res  = await fetch('/process_files', { method: 'POST', body: fd });
        const data = await res.json();
        loader.classList.add('hidden');
    
        if (!res.ok) {
            throw new Error(data.error || `Errore server ${res.status}`);
        }
        if (data.message) showMessage(data.message, 'success');
        if (typeof data.num_relazioni_estratte === 'number') {
            showMessage(`Relazioni estratte: ${data.num_relazioni_estratte}`, 'info');
        }
        if (data.report_filename) {
            const a = document.createElement('a');
            a.href = `/download_csv/${data.report_filename}`;
            a.textContent = 'Scarica Report CSV';
            a.download = data.report_filename;
            a.style.display = 'block';
            a.style.marginTop = '0.75rem';
            msgArea.append(a);
        }
        if (Array.isArray(data.warnings))
            data.warnings.forEach(w => showMessage(w, 'warning'));
        if (Array.isArray(data.errors))
            data.errors.forEach(e => showMessage(e, 'error'));
    
        if (!data.message && !data.report_filename &&
            !(data.warnings||[]).length && !(data.errors||[]).length) {
            showMessage('Nessun risultato restituito.', 'info');
        }
    
    } catch(err) {
        loader.classList.add('hidden');
        console.error(err);
        showMessage(`Errore: ${err.message}`, 'error');
    }
});

