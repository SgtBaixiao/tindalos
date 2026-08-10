/**
 * 生成进度流：public/progress.jsonl 的解析与打字机工具。
 *
 * 每行一个 JSON 事件（进度带数据源）：
 *   { "ts": "...", "agent": "kp" | "npc", "step": "步骤名", "text": "正文",
 *     "stage"?: "kp 阶段", "npc"?: "NPC 名", "action"?: "NPC 动作" }
 * 解析规则：逐行 trim，跳过空行与 `#` 注释行；JSON.parse 失败或
 * 缺少 step/text 字段的行被静默丢弃（示例文件演进时保持向前兼容）。
 */

export type ProgressAgent = 'kp' | 'npc';

export type ProgressEvent = {
  ts: string;
  agent: ProgressAgent;
  step: string;
  text: string;
  stage?: string;
  npc?: string;
  action?: string;
};

/** KP 主控步骤时间线（示例进度带的四个里程碑）。 */
export const KP_STAGES = ['读取模组', '拟定幕结构', '写作分幕', '校对付印'];

/** 解析 progress.jsonl 文本 → 进度事件列表（顺序保持）。 */
export function parseProgressEvents(raw: string): ProgressEvent[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith('#'))
    .map((line) => {
      try {
        return JSON.parse(line) as Record<string, unknown>;
      } catch {
        return null;
      }
    })
    .filter((e): e is Record<string, unknown> => e !== null && typeof e === 'object')
    .filter((e) => typeof e.step === 'string' && typeof e.text === 'string')
    .map((e) => ({
      ts: typeof e.ts === 'string' ? e.ts : '',
      agent: e.agent === 'npc' ? ('npc' as const) : ('kp' as const),
      step: e.step as string,
      text: e.text as string,
      stage: typeof e.stage === 'string' ? e.stage : undefined,
      npc: typeof e.npc === 'string' ? e.npc : undefined,
      action: typeof e.action === 'string' ? e.action : undefined,
    }));
}

/** 打字机取子串（纯函数，便于测试）：reveal 前 chars 个字符。 */
export function typewriter(text: string, chars: number): string {
  return text.slice(0, Math.max(0, chars));
}
