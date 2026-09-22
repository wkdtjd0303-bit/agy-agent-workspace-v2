/**
 * game.js - Core Game Engine for Apple Dodge
 * Manages game loop, state transitions, collision detection, scoring, and UI rendering.
 */

class SoundEffects {
    constructor() {
        this.ctx = null;
    }

    init() {
        if (!this.ctx && typeof window !== 'undefined') {
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (AudioCtx) {
                this.ctx = new AudioCtx();
            }
        }
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
    }

    playDodge() {
        if (!this.ctx) return;
        try {
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();
            osc.connect(gain);
            gain.connect(this.ctx.destination);
            osc.frequency.setValueAtTime(440, this.ctx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(880, this.ctx.currentTime + 0.08);
            gain.gain.setValueAtTime(0.1, this.ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.08);
            osc.start();
            osc.stop(this.ctx.currentTime + 0.08);
        } catch (_) {}
    }

    playCrash() {
        if (!this.ctx) return;
        try {
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();
            osc.type = 'sawtooth';
            osc.connect(gain);
            gain.connect(this.ctx.destination);
            osc.frequency.setValueAtTime(220, this.ctx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(80, this.ctx.currentTime + 0.25);
            gain.gain.setValueAtTime(0.25, this.ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.25);
            osc.start();
            osc.stop(this.ctx.currentTime + 0.25);
        } catch (_) {}
    }
}

let DefaultPlayer = (typeof Player !== 'undefined') ? Player : null;
let DefaultAppleManager = (typeof AppleManager !== 'undefined') ? AppleManager : null;
if (typeof require !== 'undefined') {
    if (!DefaultPlayer) {
        try { DefaultPlayer = require('./player').Player; } catch (_) {}
    }
    if (!DefaultAppleManager) {
        try { DefaultAppleManager = require('./apple').AppleManager; } catch (_) {}
    }
}

class GameEngine {
    constructor(canvas, PlayerClass = DefaultPlayer, AppleManagerClass = DefaultAppleManager) {
        this.canvas = canvas;
        this.ctx = canvas ? canvas.getContext('2d') : null;
        this.width = canvas ? canvas.width : 400;
        this.height = canvas ? canvas.height : 600;

        this.player = new (PlayerClass || DefaultPlayer)(this.width, this.height);
        this.appleManager = new (AppleManagerClass || DefaultAppleManager)(this.width, this.height);
        this.sound = new SoundEffects();

        this.state = 'START'; // 'START', 'PLAYING', 'GAMEOVER'
        this.score = 0;
        this.highScore = 0;
        this.inputState = { left: false, right: false };
        this.lastTime = 0;
        this.animationFrameId = null;

        this.loadHighScore();
        if (typeof window !== 'undefined') {
            this.bindEvents();
        }
    }

    loadHighScore() {
        if (typeof localStorage !== 'undefined') {
            const saved = localStorage.getItem('apple_dodge_high_score');
            if (saved) {
                this.highScore = parseInt(saved, 10) || 0;
            }
        }
    }

    saveHighScore() {
        if (this.score > this.highScore) {
            this.highScore = this.score;
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('apple_dodge_high_score', this.highScore.toString());
            }
        }
    }

    start() {
        this.sound.init();
        this.state = 'PLAYING';
        this.score = 0;
        this.player.reset();
        this.appleManager.reset();
        this.lastTime = (typeof performance !== 'undefined') ? performance.now() : 0;
    }

    gameOver() {
        this.state = 'GAMEOVER';
        this.saveHighScore();
        this.sound.playCrash();
    }

    checkCollision(boxA, boxB) {
        return (
            boxA.x < boxB.x + boxB.width &&
            boxA.x + boxA.width > boxB.x &&
            boxA.y < boxB.y + boxB.height &&
            boxA.y + boxA.height > boxB.y
        );
    }

    update(dt = 0.016) {
        if (this.state !== 'PLAYING') return;

        // Update player
        this.player.update(dt, this.inputState);

        // Update apples
        const { escapedCount } = this.appleManager.update(dt, this.score);
        if (escapedCount > 0) {
            this.score += escapedCount;
            this.sound.playDodge();
        }

        // Check collision between player and each active apple
        const playerBox = this.player.getBounds();
        for (const apple of this.appleManager.apples) {
            if (apple.active) {
                const appleBox = apple.getBounds();
                if (this.checkCollision(playerBox, appleBox)) {
                    this.gameOver();
                    break;
                }
            }
        }
    }

    render() {
        if (!this.ctx) return;
        const ctx = this.ctx;

        // Background sky gradient
        const bgGrad = ctx.createLinearGradient(0, 0, 0, this.height);
        bgGrad.addColorStop(0, '#60A5FA'); // Sky blue
        bgGrad.addColorStop(0.7, '#93C5FD');
        bgGrad.addColorStop(1, '#A7F3D0'); // Grass horizon
        ctx.fillStyle = bgGrad;
        ctx.fillRect(0, 0, this.width, this.height);

        // Ground strip
        ctx.fillStyle = '#10B981';
        ctx.fillRect(0, this.height - 20, this.width, 20);
        ctx.fillStyle = '#059669';
        ctx.fillRect(0, this.height - 8, this.width, 8);

        // Entities
        this.appleManager.draw(ctx);
        this.player.draw(ctx);

        // HUD: Score & High Score
        ctx.fillStyle = '#0F172A';
        ctx.font = 'bold 20px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText(`점수: ${this.score}`, 16, 36);

        ctx.font = '14px sans-serif';
        ctx.fillStyle = '#475569';
        ctx.fillText(`최고: ${this.highScore}`, this.width - 90, 34);

        // Overlays for Start / Game Over
        if (this.state === 'START') {
            this.renderOverlay(ctx, '🍎 사과 피하기', '화면을 터치하거나 스페이스바를 눌러 시작하세요', '좌/우 방향키 또는 화면 하단 버튼으로 이동');
        } else if (this.state === 'GAMEOVER') {
            this.renderOverlay(ctx, '💥 게임 오버', `최종 점수: ${this.score}점 (최고: ${this.highScore}점)`, '다시 시작하려면 화면을 탭하거나 스페이스바를 누르세요');
        }
    }

    renderOverlay(ctx, title, subtitle, hint) {
        ctx.save();
        ctx.fillStyle = 'rgba(15, 23, 42, 0.75)';
        ctx.fillRect(0, 0, this.width, this.height);

        ctx.textAlign = 'center';
        ctx.fillStyle = '#FFFFFF';
        ctx.font = 'bold 30px sans-serif';
        ctx.fillText(title, this.width / 2, this.height / 2 - 40);

        ctx.font = 'bold 16px sans-serif';
        ctx.fillStyle = '#FDE047'; // Gold
        ctx.fillText(subtitle, this.width / 2, this.height / 2 + 10);

        if (hint) {
            ctx.font = '13px sans-serif';
            ctx.fillStyle = '#94A3B8';
            ctx.fillText(hint, this.width / 2, this.height / 2 + 45);
        }

        // Start Button Mock Box
        ctx.fillStyle = '#EF4444';
        ctx.beginPath();
        ctx.roundRect(this.width / 2 - 80, this.height / 2 + 75, 160, 44, 22);
        ctx.fill();

        ctx.fillStyle = '#FFFFFF';
        ctx.font = 'bold 16px sans-serif';
        ctx.fillText(this.state === 'START' ? '게임 시작' : '다시 도전', this.width / 2, this.height / 2 + 103);

        ctx.restore();
    }

    tick(currentTime) {
        if (!this.lastTime) this.lastTime = currentTime;
        let dt = (currentTime - this.lastTime) / 1000;
        this.lastTime = currentTime;

        // Clamp dt to avoid huge jumps if tab was backgrounded
        if (dt > 0.1) dt = 0.1;

        this.update(dt);
        this.render();

        this.animationFrameId = requestAnimationFrame(this.tick.bind(this));
    }

    bindEvents() {
        window.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') {
                this.inputState.left = true;
            } else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') {
                this.inputState.right = true;
            } else if (e.key === ' ' || e.key === 'Enter') {
                if (this.state !== 'PLAYING') {
                    this.start();
                }
            }
        });

        window.addEventListener('keyup', (e) => {
            if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') {
                this.inputState.left = false;
            } else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') {
                this.inputState.right = false;
            }
        });

        if (this.canvas) {
            this.canvas.addEventListener('pointerdown', (e) => {
                if (this.state !== 'PLAYING') {
                    this.start();
                }
            });
        }
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { GameEngine, SoundEffects };
}
