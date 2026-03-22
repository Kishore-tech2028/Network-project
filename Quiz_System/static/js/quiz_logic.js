let timeLeft = 10;
let selectedBtn = null;
let timerInterval;

async function fetchQuestion() {
    try {
        const response = await fetch('/api/score/'); 
        const result = await response.json();
        
        if (result.type === "question") {
            renderQuestion(result);
            startTimer(10); // Fairness timeout
        } else if (result.type === "final_score") {
            showEndScreen(result);
        }
    } catch (err) {
        console.error("Connection Error:", err);
    }
}

function renderQuestion(result) {
    document.getElementById('question-title').innerText = result.data.question;
    const optionsDiv = document.getElementById('options');
    optionsDiv.innerHTML = '';
    document.getElementById('status-msg').innerText = "Select an option to lock it.";

    // Handle Optional Images
    const img = document.getElementById('q-image');
    if (result.data.image_url) {
        img.src = result.data.image_url;
        img.style.display = 'block';
    } else {
        img.style.display = 'none';
    }

    result.data.options.forEach(opt => {
        const btn = document.createElement('button');
        btn.className = 'option-btn';
        btn.innerText = opt;
        btn.onclick = () => lockOption(btn);
        optionsDiv.appendChild(btn);
    });
}

function lockOption(btn) {
    if (selectedBtn) selectedBtn.classList.remove('locked');
    selectedBtn = btn;
    btn.classList.add('locked');
    document.getElementById('status-msg').innerText = "Choice Locked. Revealing soon...";
}

function startTimer(seconds) {
    timeLeft = seconds;
    clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        timeLeft--;
        document.getElementById('timer-fill').style.width = (timeLeft * 10) + "%";
        if (timeLeft <= 0) {
            clearInterval(timerInterval);
            handleTimeout();
        }
    }, 1000);
}

function handleTimeout() {
    const buttons = document.querySelectorAll('.option-btn');
    buttons.forEach(b => b.disabled = true);
    document.getElementById('status-msg').innerText = "Time up! Synchronizing scores...";
    
    // Brief delay before fetching next question to show locked choice
    setTimeout(fetchQuestion, 2000);
}

function showEndScreen(result) {
    document.getElementById('quiz-content').innerHTML = `
        <h2 style="color: var(--primary)">Quiz Complete!</h2>
        <p>Your results have been recorded in the secure backend.</p>
        <div style="font-size: 3rem; margin: 1rem 0;">🏆</div>
        <button class="option-btn" onclick="location.reload()">Restart Quiz</button>
    `;
}

window.onload = fetchQuestion;