import { monsterForBug } from "../pixel/cast.js";
import { textureKey } from "../pixel/textures.js";
import { bestiaryFromState, projectBestiary, openBugs } from "../hud/bestiary.js";

/** Persistent map sprites, independent of T16's room/corridor redraw objects.
 * Logical state updates immediately; a closing monster's visual lingers only
 * through its defeat tween. Match-media changes settle every in-flight effect.
 */
export function createMapAnimations(browser = window) {
  let state = bestiaryFromState({}), scene = null, roomId = null, flash = false;
  const party = new Map(), monsters = new Map();
  const media = browser.matchMedia("(prefers-reduced-motion: reduce)");
  let reduced = media.matches;
  const stop = sprite => scene?.tweens?.killTweensOf(sprite);
  const position = (sprite, point) => { sprite.setPosition?.(point.x, point.y); sprite.x = point.x; sprite.y = point.y; };
  const texture = (sprite, key) => sprite.setTexture?.(key);
  const alpha = (sprite, value) => { sprite.setAlpha?.(value); sprite.alpha = value; };
  function settle() {
    for (const [role, member] of party) {
      stop(member.sprite); position(member.sprite,member.target); alpha(member.sprite,1);
      texture(member.sprite,textureKey(role,"idle"));
    }
    for (const [id, monster] of monsters) if (monster.closing) {
      stop(monster.sprite); monster.sprite.destroy(); monsters.delete(id);
    }
  }
  const motionChange = event => { reduced = event.matches; if (reduced) settle(); };
  media.addEventListener("change",motionChange);
  return {
    setState(snapshot) {
      state = bestiaryFromState(snapshot); roomId = null; flash = false;
      for (const member of party.values()) { stop(member.sprite); member.sprite.destroy(); }
      for (const monster of monsters.values()) { stop(monster.sprite); monster.sprite.destroy(); }
      party.clear(); monsters.clear();
    },
    onEvent(event) {
      state = projectBestiary(state,event);
      if (event.type === "respawn") flash = true;
    },
    render(currentScene,layout,activeRoom) {
      scene = currentScene;
      const room = layout.rooms.find(room => room.id === activeRoom);
      if (room) {
        ["mage","warrior","paladin"].forEach((role,index) => {
          const target = {x:room.x + 24 + index * 16,y:room.y + 24};
          let member = party.get(role);
          if (!member) {
            member = {sprite:scene.add.image(target.x,target.y,textureKey(role,"idle")),target};
            party.set(role,member);
          }
          member.sprite.setVisible?.(true);
          const changed = member.target.x !== target.x || member.target.y !== target.y;
          member.target = target;
          if (changed) {
            stop(member.sprite); alpha(member.sprite,1);
            if (!reduced && !layout.overview && scene.tweens && roomId !== null) {
              texture(member.sprite,textureKey(role,"walk"));
              scene.tweens.add({targets:member.sprite,x:target.x,y:target.y,duration:500,
                ease:"Linear",onUpdate:tween => texture(member.sprite,textureKey(role,"walk",Math.floor(tween.progress * 4) % 2)),
                onComplete:() => texture(member.sprite,textureKey(role,"idle"))});
            } else { position(member.sprite,target); texture(member.sprite,textureKey(role,"idle")); }
          }
          if (flash && !reduced && scene.tweens) {
            stop(member.sprite); position(member.sprite,target);
            texture(member.sprite,textureKey(role,"idle"));
            scene.tweens.add({targets:member.sprite,alpha:0.2,duration:100,yoyo:true,repeat:2,
              onComplete:() => alpha(member.sprite,1)});
          }
        });
      } else {
        for (const [role,member] of party) {
          stop(member.sprite); position(member.sprite,member.target); alpha(member.sprite,1);
          texture(member.sprite,textureKey(role,"idle")); member.sprite.setVisible?.(false);
        }
      }
      roomId = activeRoom || roomId; flash = false;
      const bugs = openBugs(state), openIds = new Set(bugs.map(bug => bug.bug_id));
      for (const [id,monster] of monsters) {
        if (!openIds.has(id) && !monster.closing) {
          monster.closing = true;
          if (reduced || !scene.tweens || layout.overview) {
            monster.sprite.destroy(); monsters.delete(id);
          } else {
            texture(monster.sprite,textureKey(monsterForBug(id),"defeated"));
            scene.tweens.add({targets:monster.sprite,alpha:0,duration:400,
              onComplete:() => { monster.sprite.destroy(); if (monsters.get(id) === monster) monsters.delete(id); }});
          }
        }
      }
      for (const bug of bugs) {
        const room = layout.rooms.find(room => room.id === bug.room_id);
        if (!room) continue; // A feature-level bug has no invented map location.
        let monster = monsters.get(bug.bug_id);
        if (monster?.closing) { stop(monster.sprite); monster.sprite.destroy(); monsters.delete(bug.bug_id); monster = null; }
        const siblings = bugs.filter(other => other.room_id === bug.room_id).sort((a,b) => a.bug_id.localeCompare(b.bug_id));
        const index = siblings.findIndex(other => other.bug_id === bug.bug_id);
        const x = room.x + room.width - 24 - Math.min(index, 2) * 16;
        const y = room.y + 24;
        if (!monster) {
          monster = {sprite:scene.add.image(x,y,textureKey(monsterForBug(bug.bug_id),"idle")),closing:false};
          monsters.set(bug.bug_id,monster);
        }
        position(monster.sprite,{x,y}); monster.sprite.setVisible?.(true);
      }
      // T16 replaces graphics on each event. Persistent sprites must be raised
      // above that new paint, including a monster still completing its defeat.
      for (const member of party.values()) scene.children?.bringToTop(member.sprite);
      for (const monster of monsters.values()) scene.children?.bringToTop(monster.sprite);
      return room ? party.get("warrior")?.sprite : null;
    },
    destroy() {
      settle();
      for (const member of party.values()) member.sprite.destroy();
      for (const monster of monsters.values()) { stop(monster.sprite); monster.sprite.destroy(); }
      party.clear(); monsters.clear(); media.removeEventListener("change",motionChange);
    },
  };
}
