/** Pure placement in integer 16px tiles. Floors follow numeric area order,
 * including empty areas. Depth is local to a floor; cross-floor dependencies
 * still lock doors. Input arrays and their metadata are never mutated.
 * Three-tile chambers let the ten-floor repository castle fit at zoom 1 in
 * 960×540. Shared arena columns leave halls on shorter floors and a continuous
 * stairwell outside the right wall, connecting arenas on adjacent floors.
 */
export function castleLayout(world) {
  const compareId = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  const areas = [...world.areas].sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
  const byId = new Map(world.rooms.map(room => [room.id, room]));
  const dependencies = new Map(world.rooms.map(room => [room.id,
    [...new Set([...(room.depends_on || []), ...world.doors.filter(door => door.dst === room.id).map(door => door.src)])].sort()]));
  const depths = new Map(), visiting = new Set();
  function depth(room) {
    if (depths.has(room.id)) return depths.get(room.id);
    if (visiting.has(room.id)) throw new Error("Cyclic room doors");
    visiting.add(room.id);
    const sources = dependencies.get(room.id).map(id => byId.get(id));
    if (sources.some(source => !source)) throw new Error("Door has an unknown room");
    const local = sources.filter(source => source.area === room.area);
    const value = local.length ? 1 + Math.max(...local.map(depth)) : 0;
    visiting.delete(room.id); depths.set(room.id, value);
    return value;
  }
  const ordered = areas.map(area => world.rooms.filter(room => room.area === area.id)
    .map(room => ({ ...room, depends_on: dependencies.get(room.id), depth: depth(room),
      state: room.state || (dependencies.get(room.id).length ? "locked" : "open") }))
    .sort((a, b) => a.depth - b.depth || compareId(a, b)));
  const arenaX = 8 + Math.max(1, ...ordered.map(rooms => rooms.length)) * 6;
  const floors = areas.map((area, index) => {
    const y = 2 + index * 3; // Leave room for the desktop's drag title bar.
    const chambers = ordered[index].map((room, column) => ({ ...room,
      x: 8 + column * 6, y, width: 5, height: 3 }));
    const boss = { id: `${area.id}:boss`, area: area.id, x: arenaX, y, width: 5, height: 3 };
    return { ...area, boss_ids: [...(area.boss_ids || [])].sort(),
      x: 1, y, width: arenaX + 4, height: 3, chambers, boss };
  });
  const chambers = floors.flatMap(floor => floor.chambers);
  const doors = floors.flatMap(floor => floor.chambers.slice(1).map((room, index) => {
    const source = floor.chambers[index];
    return { src: source.id, dst: room.id, area: floor.id,
      locked: !room.depends_on.every(id => byId.get(id)?.state === "cleared"),
      x: source.x + source.width, y: room.y, width: 1, height: 3,
      points: [{ x: source.x + source.width, y: room.y + 2 }, { x: room.x, y: room.y + 2 }] };
  }));
  const stairs = floors.slice(0, -1).map((floor, index) => ({
    src: floor.boss.id, dst: floors[index + 1].boss.id,
    from_area: floor.id, to_area: floors[index + 1].id,
    x: arenaX + 5, y: floor.y + 1, width: 1, height: 4,
    points: [{ x: arenaX + 5, y: floor.y + 2 },
      { x: arenaX + 6, y: floor.y + 2 }, { x: arenaX + 6, y: floor.y + 5 },
      { x: arenaX + 5, y: floor.y + 5 }],
  }));
  return { floors, chambers, doors, stairs, width: arenaX + 8,
    height: Math.max(5, 3 + floors.length * 3) };
}
