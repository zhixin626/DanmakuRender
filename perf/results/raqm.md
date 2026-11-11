# 第 13 项：Pillow raqm

状态：BASIC/RAQM 选择与相对 DLL 加载代码完成；已按官方 MSYS2 源限时尝试安装 fribidi，但当前 Pillow wheel 仍未启用 RAQM，因此默认保持 BASIC。

1. **改了什么**：`SpriteFactory` 增加 `layout_engine` 选择；`auto` 优先检查 Pillow RAQM，BASIC 作为安全回退；按活动 Python 的 `Library/bin`、`bin`、`DLLs` 相对目录加载 DLL；GPU atlas、PillowText、面板和默认配置透传选择。
2. **为什么这样改**：为复杂文字保留可选的 FriBidi/HarfBuzz 排版能力，同时在依赖缺失时维持现有 BASIC 行为，不写死机器路径或下载未知 DLL。
3. **遇到的问题和解决办法**：从官方 MSYS2 UCRT64 包 `mingw-w64-ucrt-x86_64-fribidi-1.0.18-1-any.pkg.tar.zst` 提取 DLL；包 SHA256 为 `4837D3626349C617AAA2DB3D116F9B7EA208B11644F507A6A83EAB36BB3AC920`，DLL SHA256 为 `4C775E98B38991C93AFFAF71F088306B37FD2C0D94FF8A90C4906DB4669746C9`。Pillow 11.3.0 仍报 `features.check('raqm') == False`，因此不伪造 RAQM 截图，默认改为 BASIC。
4. **意见和想法**：双向控制符仍由第8项 clean_text 删除；若用户后续通过 MSYS2/conda-forge 安装依赖，`layout_engine: auto` 会自动启用，中文/英文/Emoji 默认仍走 BASIC。
5. **整体看法**：代码路径和回退行为已覆盖，相关测试 30 passed；RAQM 的多语种视觉改善需可信 DLL 安装后再验收。
6. **给 Claude 的建议**：请确认相对 DLL 搜索路径和“无可信依赖不启用”的处理；当前不具备生成开启前后对比截图的外部条件，建议将安装后的短片段对比留到同一空闲窗口补测。

环境证据：Pillow 11.3.0，`features.check('raqm')=False`；未找到 `libfribidi-0.dll`/`fribidi.dll`。未提交任何 DLL。
