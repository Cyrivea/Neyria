/**
 * Cmd+K 命令面板的纯逻辑（UI2 第二批④）：命令注册项与查询过滤。
 * 组件只负责交互壳；过滤规则抽在这里才能脱离 DOM 单测。
 */

export interface PaletteCommand {
  id: string
  title: string
  /** 一行小字说明，告诉用户这个命令会干什么 */
  meta?: string
  /** 额外可被搜索命中的关键词（英文/拼音/别名） */
  keywords?: string[]
}

/** 子串模糊匹配：标题 > 关键词 > meta；全部转小写，中英文一视同仁 */
export function filterPaletteCommands(commands: PaletteCommand[], query: string): PaletteCommand[] {
  const q = query.trim().toLowerCase()
  if (!q) return commands
  return commands.filter((command) => {
    const haystacks = [command.title, ...(command.keywords ?? []), command.meta ?? '']
    return haystacks.some((text) => text.toLowerCase().includes(q))
  })
}
