# 桌面宠物网页

React 19、TypeScript、Vite、Ant Design 和 Zustand。当前界面采用 Notion 风格；GSAP 管理页面与交互动效，React Bits ClickSpark 仅用于概览名片。

```powershell
npm ci
npm run dev
```

开发端口默认 5173；`/xiaozhi` 代理到 Java 服务，见 `vite.config.ts`。
构建运行 `npm run build`，静态检查运行 `npm run lint`。

- 页面入口：`src/router/` 与 `src/pages/`。
- 外观：`src/styles/notion-theme.css`；页面样式位于各页面目录。
- 动效：`src/components/motion/` 和 `src/styles/interaction-motion.css`。
- 提醒弹窗：`src/layouts/ReminderPopup.tsx`；记忆分类颜色：`src/pages/memory/memory-list.css`。
- 第三方组件来源与许可证：`src/components/react-bits/`。

聊天记录、歌词与弹窗保留原生滚动；新增桌面动效尊重减少动态效果偏好。
`output/` 保存本地截图等验证产物，不提交。

整体启动方式见[项目说明](../README.md)，模块说明见[文档索引](../docs/README.md)。
