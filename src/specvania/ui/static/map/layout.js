/** Pure, deterministic placement. Columns are local door depth, rows room id.
 * Earlier areas always precede later areas. Cross-area doors do not add depth.
 * Both views use integer pixels; overview changes geometry, never camera zoom.
 */
export function worldFromRooms(rooms, areas = [], doors = []) {
  const sorted = rooms.map(room => ({ ...room,
    state: room.state || (room.depends_on.length ? "locked" : "open") }))
    .sort((a, b) => a.id.localeCompare(b.id, "en"));
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

export function layoutWorld(world, overview = false) {
  const roomsById = new Map(world.rooms.map(room => [room.id, room]));
  const depths = new Map();
  const visiting = new Set();
  function depth(room) {
    if (depths.has(room.id)) return depths.get(room.id);
    if (visiting.has(room.id)) throw new Error("Cyclic room doors");
    visiting.add(room.id);
    const sources = room.depends_on.map(id => roomsById.get(id))
      .filter(source => source && source.area === room.area);
    const value = sources.length ? 1 + Math.max(...sources.map(depth)) : 0;
    visiting.delete(room.id);
    depths.set(room.id, value);
    return value;
  }
  const areas = [...world.areas].sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
  let columnCount = 0;
  let maxRows = 1;
  const placements = areas.map(area => {
    const groups = new Map();
    for (const room of world.rooms.filter(room => room.area === area.id)
      .sort((a, b) => a.id.localeCompare(b.id, "en"))) {
      const column = depth(room);
      const group = groups.get(column) || [];
      group.push(room);
      groups.set(column, group);
      maxRows = Math.max(maxRows, group.length);
    }
    const columns = groups.size ? Math.max(...groups.keys()) + 1 : 1;
    const start = columnCount;
    columnCount += columns + 1; // A blank column between areas.
    return { ...area, start, columns, groups };
  });
  columnCount = Math.max(1, columnCount - 1);
  const stepX = overview ? Math.min(24, Math.floor(304 / columnCount)) : 96;
  const stepY = overview ? Math.min(32, Math.floor(144 / maxRows)) : 72;
  const width = overview ? Math.max(1, stepX - 4) : 72;
  const height = overview ? Math.max(1, stepY - 8) : 48;
  const rooms = placements.flatMap(area => [...area.groups.entries()].flatMap(([column, group]) =>
    group.map((room, row) => ({ ...room, depth: column, x: 8 + (area.start + column) * stepX,
      y: 28 + row * stepY, width, height }))));
  const placed = new Map(rooms.map(room => [room.id, room]));
  const doors = world.doors.map(door => {
    const src = placed.get(door.src);
    const dst = placed.get(door.dst);
    if (!src || !dst) throw new Error("Door has an unknown room");
    const start = { x: src.x + src.width, y: src.y + Math.floor(src.height / 2) };
    const end = { x: dst.x, y: dst.y + Math.floor(dst.height / 2) };
    const middleX = Math.floor((start.x + end.x) / 2);
    return { ...door, points: [start, { x: middleX, y: start.y }, { x: middleX, y: end.y }, end] };
  });
  return { rooms, doors, areas: placements.map(({ groups, ...area }) => ({ ...area,
    x: 8 + area.start * stepX, y: 8 })),
    width: Math.max(320, 16 + columnCount * stepX),
    height: Math.max(180, 32 + maxRows * stepY), overview };
}

export function integerDisplayZoom(width, height) {
  return Math.max(1, Math.floor(Math.min(width / 320, height / 180)));
}
