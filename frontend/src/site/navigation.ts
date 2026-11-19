import type { IconName } from '../ui/Icon';
export const NAVIGATION: {
  route: string;
  title: string;
  short: string;
  icon: IconName;
  description: string;
}[] = [
  {
    route: 'workbench',
    title: '剧本工作台',
    short: '工作台',
    icon: 'grid',
    description: '整理幕与场景，编辑人物、事件和线索。',
  },
  {
    route: 'library',
    title: '模组资料库',
    short: '资料库',
    icon: 'book',
    description: '保存原始模组，在正文与图像中查找依据。',
  },
  {
    route: 'qa',
    title: '规则问答',
    short: '问答',
    icon: 'search',
    description: '提出具体问题，回到资料中的出处。',
  },
  {
    route: 'history',
    title: '历史记录',
    short: '历史',
    icon: 'history',
    description: '打开已有剧本，回看准备过的内容。',
  },
  {
    route: 'eval',
    title: '评测',
    short: '评测',
    icon: 'check',
    description: '检查当前修订，留下采用与待改的理由。',
  },
  {
    route: 'memories',
    title: '记忆',
    short: '记忆',
    icon: 'memory',
    description: '区分备团计划与实际记录，接续下一场。',
  },
];
