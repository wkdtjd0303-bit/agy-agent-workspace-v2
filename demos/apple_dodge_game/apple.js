/**
 * apple.js - Falling Apple Entity & Spawner Pool
 * Manages falling physics, progressive difficulty acceleration, and clean lifecycle pooling.
 */

class Apple {
    constructor() {
        this.reset(0, -30, 200);
    }

    reset(x, y, speed = 200, radius = 14) {
        this.x = x;
        this.y = y;
        this.radius = radius;
        this.speed = speed;
        this.active = true;
        this.rotation = (Math.random() - 0.5) * 0.4;
    }

    update(dt = 0.016) {
        if (!this.active) return;
        this.y += this.speed * dt;
    }

    getBounds() {
        return {
            x: this.x - this.radius,
            y: this.y - this.radius,
            width: this.radius * 2,
            height: this.radius * 2
        };
    }

    draw(ctx) {
        if (!ctx || !this.active) return;
        ctx.save();
        ctx.translate(this.x, this.y);
        ctx.rotate(this.rotation);

        // Apple body
        ctx.fillStyle = '#EF4444'; // Bright crimson
        ctx.beginPath();
        ctx.arc(-this.radius * 0.4, 0, this.radius * 0.75, 0, Math.PI * 2);
        ctx.arc(this.radius * 0.4, 0, this.radius * 0.75, 0, Math.PI * 2);
        ctx.arc(0, this.radius * 0.2, this.radius * 0.75, 0, Math.PI * 2);
        ctx.fill();

        // Highlight
        ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
        ctx.beginPath();
        ctx.ellipse(-this.radius * 0.35, -this.radius * 0.3, this.radius * 0.25, this.radius * 0.4, -0.4, 0, Math.PI * 2);
        ctx.fill();

        // Stem
        ctx.strokeStyle = '#78350F';
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(0, -this.radius * 0.6);
        ctx.quadraticCurveTo(3, -this.radius * 1.1, 4, -this.radius * 1.25);
        ctx.stroke();

        // Leaf
        ctx.fillStyle = '#10B981'; // Fresh leaf green
        ctx.beginPath();
        ctx.ellipse(5, -this.radius * 0.9, 4, 2, 0.6, 0, Math.PI * 2);
        ctx.fill();

        ctx.restore();
    }
}

class AppleManager {
    constructor(canvasWidth = 400, canvasHeight = 600) {
        this.canvasWidth = canvasWidth;
        this.canvasHeight = canvasHeight;
        this.pool = [];
        this.apples = [];
        this.spawnTimer = 0;
        this.spawnInterval = 0.8; // seconds between spawns
        this.baseSpeed = 220;     // pixels per second
        this.speedMultiplier = 1.0;
        this.totalCreated = 0;
    }

    reset() {
        for (const apple of this.apples) {
            apple.active = false;
            this.pool.push(apple);
        }
        this.apples = [];
        this.spawnTimer = 0;
        this.spawnInterval = 0.8;
        this.speedMultiplier = 1.0;
    }

    spawnApple() {
        let apple = this.pool.pop();
        if (!apple) {
            apple = new Apple();
            this.totalCreated += 1;
        }
        const margin = 24;
        const x = margin + Math.random() * (this.canvasWidth - margin * 2);
        const y = -20;
        const speed = (this.baseSpeed + Math.random() * 80) * this.speedMultiplier;
        apple.reset(x, y, speed);
        this.apples.push(apple);
        return apple;
    }

    update(dt = 0.016, score = 0) {
        // Difficulty scaling based on score
        this.speedMultiplier = 1.0 + Math.min(1.5, score * 0.03);
        this.spawnInterval = Math.max(0.35, 0.8 - score * 0.008);

        this.spawnTimer += dt;
        if (this.spawnTimer >= this.spawnInterval) {
            this.spawnTimer = 0;
            this.spawnApple();
        }

        const activeApples = [];
        let escapedCount = 0;

        for (const apple of this.apples) {
            apple.update(dt);
            if (apple.y > this.canvasHeight + 30) {
                apple.active = false;
                this.pool.push(apple);
                escapedCount += 1;
            } else {
                activeApples.push(apple);
            }
        }
        this.apples = activeApples;
        return { escapedCount };
    }

    draw(ctx) {
        for (const apple of this.apples) {
            apple.draw(ctx);
        }
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Apple, AppleManager };
}
