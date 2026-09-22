/**
 * player.js - Player Entity for Apple Dodge Game
 * Handles player position, movement speed, boundary clamping, and drawing.
 */

class Player {
    constructor(canvasWidth = 400, canvasHeight = 600) {
        this.canvasWidth = canvasWidth;
        this.canvasHeight = canvasHeight;
        this.width = 44;
        this.height = 44;
        this.speed = 360; // pixels per second
        this.reset();
    }

    reset() {
        this.x = (this.canvasWidth - this.width) / 2;
        this.y = this.canvasHeight - this.height - 20;
        this.vx = 0;
    }

    moveLeft(dt = 0.016) {
        this.x = Math.max(0, this.x - this.speed * dt);
    }

    moveRight(dt = 0.016) {
        this.x = Math.min(this.canvasWidth - this.width, this.x + this.speed * dt);
    }

    update(dt = 0.016, inputState = {}) {
        if (inputState.left) {
            this.moveLeft(dt);
        }
        if (inputState.right) {
            this.moveRight(dt);
        }
    }

    getBounds() {
        return {
            x: this.x + 4,
            y: this.y + 4,
            width: this.width - 8,
            height: this.height - 8
        };
    }

    draw(ctx) {
        if (!ctx) return;
        ctx.save();
        // Basket / Player Body
        const cx = this.x + this.width / 2;
        const cy = this.y + this.height / 2;

        // Shadow
        ctx.fillStyle = 'rgba(0, 0, 0, 0.2)';
        ctx.beginPath();
        ctx.ellipse(cx, this.y + this.height + 2, this.width / 2, 6, 0, 0, Math.PI * 2);
        ctx.fill();

        // Basket Base
        ctx.fillStyle = '#D97706'; // Warm amber/wood
        ctx.beginPath();
        ctx.roundRect(this.x, this.y + 12, this.width, this.height - 12, [4, 4, 10, 10]);
        ctx.fill();

        // Rim
        ctx.fillStyle = '#B45309';
        ctx.beginPath();
        ctx.roundRect(this.x - 3, this.y + 8, this.width + 6, 8, 4);
        ctx.fill();

        // Woven pattern lines
        ctx.strokeStyle = '#92400E';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(this.x + 10, this.y + 16);
        ctx.lineTo(this.x + 14, this.y + this.height);
        ctx.moveTo(this.x + 22, this.y + 16);
        ctx.lineTo(this.x + 22, this.y + this.height);
        ctx.moveTo(this.x + 34, this.y + 16);
        ctx.lineTo(this.x + 30, this.y + this.height);
        ctx.stroke();

        ctx.restore();
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Player };
}
