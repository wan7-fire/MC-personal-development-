# AI Aligner MVP

一个 TypeScript CLI 原型，用于扫描项目接口，并将 AI 生成的代码片段与项目接口做基础对齐。

## 安装

```bash
npm install
```

## 运行 Demo

```bash
npm run dev -- align --project ./examples/demo-project --snippet ./examples/snippet.ts
```

## 当前 MVP 能力

- 扫描 TypeScript 项目中的导出函数
- 扫描 interface/type 字段
- 解析 snippet 中的函数调用和 imports
- 函数名精确匹配
- 将多参数调用改写为单 DTO 对象参数调用
- 自动补充缺失 import
- 输出 diff 和报告
