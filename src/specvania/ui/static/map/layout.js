import { castleLayout } from "./castle.js";
import { TILE_SIZE } from "../pixel/cast.js";

/** Reconnects retain area metadata on rooms; old recordings need no new event. */
export function worldFromRooms(rooms, areas = [], doors = []) {
  const sorted = rooms.map(room => ({ ...room, depends_on: [...(room.depends_on || [])].sort(),
    state: room.state || (room.depends_on?.length ? "locked" : "open") }))
    .sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const areaIds = [...new Set(sorted.map(room => room.area))]
    .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
  return {
    rooms: sorted,
    areas: areas.length ? [...areas] : areaIds.map(id => {
      const room = sorted.find(room => room.area === id);
      return { id, title: room.area_title || id, boss_ids: room.boss_ids || [] };
    }),
    doors: doors.length ? [...doors] : sorted.flatMap(room =>
      room.depends_on.map(src => ({ src, dst: room.id }))),
  };
}

/** The sole pixel adapter. Overview uses this same geometry and a camera zoom. */
export function layoutWorld(world, overview = false) {
  const castle = castleLayout(world);
  const pixels = chamber => ({ ...chamber, x: chamber.x * TILE_SIZE, y: chamber.y * TILE_SIZE,
    width: chamber.width * TILE_SIZE, height: chamber.height * TILE_SIZE });
  const points = path => path.map(point => ({ x: point.x * TILE_SIZE, y: point.y * TILE_SIZE }));
  return {
    rooms: castle.chambers.map(pixels),
    areas: castle.floors.map(floor => ({ ...pixels(floor), boss: pixels(floor.boss) })),
    bosses: castle.floors.map(floor => pixels(floor.boss)),
    doors: castle.doors.map(door => ({ ...pixels(door), points: points(door.points) })),
    stairs: castle.stairs.map(stair => ({ ...pixels(stair), points: points(stair.points) })),
    width: castle.width * TILE_SIZE, height: castle.height * TILE_SIZE, overview,
  };
}

export function integerDisplayZoom(width, height) {
  return Math.max(1, Math.floor(Math.min(width / 320, height / 180)));
}

export function castleViewZoom(width, height, layout, overview) {
  return overview ? Math.max(1, Math.floor(Math.min(width / layout.width, height / layout.height)))
    : integerDisplayZoom(width, height);
}
