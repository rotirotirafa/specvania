/** Pure journey projection. Events are the ordered prefix already displayed (or
 * the suffix after state); never pass events ahead of a replay's cursor.
 * screen is the current screen/submode, not the screen that launched a replay.
 */
export function deriveStep({studio = {}, state = {}, screen = "", events = [], replay = false} = {}) {
  const recorded = Boolean(replay || studio.replay || studio.phase === "replaying");
  let number = 1, roomId = state.active_room || state.party_room || null;
  if (recorded) {
    const planned = studio.replay?.stream === "plan" && Boolean(state.feature);
    number = studio.replay?.stream === "plan" ? (planned ? 4 : 3) : 5;
    if (["finished", "aborted"].includes(state.status) && studio.replay?.stream !== "plan") number = 6;
    const timeline = [...events];
    if (!timeline.length && studio.replay?.dialogue && !planned) timeline.push(studio.replay.dialogue);
    for (const event of timeline) {
      if (["clarify_question", "clarify_answer"].includes(event.type)) number = 3;
      else if (event.type === "spec_written") number = 4;
      else if (event.type === "run_started") {number = 5; roomId = null;}
      else if (event.type === "run_finished") number = 6;
      if (event.type === "room_entered") roomId = event.room_id;
    }
  } else {
    number = {idle:1, project_open:1, planning:3, world_ready:4, running:5, finished:6}[studio.phase] || 1;
    if (["title", "project", "settings", "starting", "error", "probe"].includes(screen)) number = 1;
    else if (screen === "idea") number = {planning:3, world_ready:4}[studio.phase] || 2;
    else if (["clarify", "working"].includes(screen)) number = 3;
    else if (["map", "world", "saving"].includes(screen)) number = 4;
    else if (["castle", "running", "approval", "pause", "boss"].includes(screen)) number = 5;
    else if (screen === "end") number = 6;
    for (const event of events) if (event.type === "room_entered") roomId = event.room_id;
  }
  const id = ["project", "idea", "clarify", "map", "expedition", "end"][number - 1];
  return {number, total:6, id, labelKey:`play.step.${id}`, roomId:number === 5 ? roomId : null};
}
