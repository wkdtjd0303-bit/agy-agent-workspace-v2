/**
 * game.test.js - Mechanical Verification Test Suite for Apple Dodge Game
 * Tests the 4 core phases under the Commander Closed-Loop Quality Contract.
 */

const assert = require('assert');
const { Player } = require('./player');
const { Apple, AppleManager } = require('./apple');
const { GameEngine } = require('./game');

console.log('🧪 [AGY Closed-Loop Engine] Starting Mechanical Verification Test Suite...\n');

// -------------------------------------------------------------
// Turn 1 Test: Engine Initialization & Lifecycle Contract
// -------------------------------------------------------------
console.log('▶ [Turn 1 Verification]: Core Game Engine State & Lifecycle');
{
    const game = new GameEngine(null); // Headless engine
    assert.strictEqual(game.state, 'START', 'Initial state must be START');
    assert.strictEqual(game.score, 0, 'Initial score must be 0');

    game.start();
    assert.strictEqual(game.state, 'PLAYING', 'Calling start() must transition state to PLAYING');
    console.log('  ✔ Engine initial state transition passed.');
}

// -------------------------------------------------------------
// Turn 2 Test: Player Control & Boundary Clamping
// -------------------------------------------------------------
console.log('\n▶ [Turn 2 Verification]: Player Mechanics & Boundary Clamping');
{
    const player = new Player(400, 600);
    const initialX = player.x;

    // Move left
    player.update(0.1, { left: true, right: false });
    assert.ok(player.x < initialX, 'Player X must decrease when moving left');

    // Clamp left boundary
    for (let i = 0; i < 30; i++) {
        player.update(0.1, { left: true, right: false });
    }
    assert.strictEqual(player.x, 0, 'Player X must not go below 0 (left boundary clamp)');

    // Clamp right boundary
    for (let i = 0; i < 50; i++) {
        player.update(0.1, { left: false, right: true });
    }
    assert.strictEqual(player.x, 400 - player.width, 'Player X must not exceed canvasWidth - width (right boundary clamp)');
    console.log('  ✔ Player movement and boundary clamping passed.');
}

// -------------------------------------------------------------
// Turn 3 Test: Apple Falling Physics & Object Pooling
// -------------------------------------------------------------
console.log('\n▶ [Turn 3 Verification]: Apple Falling Physics & Spawner Pooling');
{
    const manager = new AppleManager(400, 600);
    manager.spawnApple();
    assert.strictEqual(manager.apples.length, 1, 'Spawn should create exactly 1 active apple');

    const apple = manager.apples[0];
    const startY = apple.y;

    // Simulate 1 second of falling (60 frames)
    for (let frame = 0; frame < 60; frame++) {
        manager.update(1 / 60, 0);
    }
    assert.ok(apple.y > startY, 'Apple Y must increase as it falls');

    // Simulate 300 frames of running
    let totalEscaped = 0;
    for (let frame = 0; frame < 300; frame++) {
        const res = manager.update(1 / 60, 0);
        totalEscaped += res.escapedCount;
    }
    assert.ok(totalEscaped > 0, 'Apples must escape off-screen as they fall');
    assert.ok(manager.totalCreated < 6, `Total object allocations (${manager.totalCreated}) must be bounded due to pooling recycling`);
    console.log(`  ✔ Apple falling physics and object pooling passed (Escaped: ${totalEscaped}, Total Allocations: ${manager.totalCreated}).`);
}

// -------------------------------------------------------------
// Turn 4 Test: Collision Detection, Score Increment & Game Over
// -------------------------------------------------------------
console.log('\n▶ [Turn 4 Verification]: Collision Detection, Scoring & Game Over');
{
    const game = new GameEngine(null);
    game.start();

    // 1. Test score accumulation when apple escapes bottom
    const testApple = new Apple();
    testApple.reset(200, 610, 300);
    game.appleManager.apples = [testApple];

    game.update(0.1); // 610 + 30 = 640 > 630 -> escapedCount = 1
    assert.strictEqual(game.score, 1, 'Score must increment by 1 when apple is dodged');

    // 2. Test collision detection when apple collides directly with player
    game.start();
    const playerBounds = game.player.getBounds();
    const collidingApple = new Apple();
    collidingApple.reset(playerBounds.x + 5, playerBounds.y + 5, 100);
    game.appleManager.apples = [collidingApple];

    game.update(0.016);
    assert.strictEqual(game.state, 'GAMEOVER', 'State must transition to GAMEOVER on collision');
    console.log('  ✔ Collision detection and game-over state transition passed.');
}

console.log('\n════════════════════════════════════════════════════════════════════');
console.log('🎉 [ALL 4 TURN TESTS PASSED] 100% Mechanical Evidence Verified!');
console.log('════════════════════════════════════════════════════════════════════\n');
process.exit(0);
