---
name: pdf
description: Create PDF files and send them to users via Telegram/channel using send_file tool.
tags: [pdf, file, telegram, channel]
---

# PDF 创建与发送

## 创建 PDF

使用 Python 生成 PDF 文件。推荐使用系统内置库：

### 方法 1: 使用 exec 工具 + reportlab（首选）

```tool
{"tool": "exec", "params": {"cmd": "pip install reportlab -q && python3 -c \"\nfrom reportlab.lib.pagesizes import A4\nfrom reportlab.pdfgen import canvas\nc = canvas.Canvas('/tmp/output.pdf', pagesize=A4)\nc.drawString(72, 750, 'Hello World')\nc.save()\nprint('PDF created: /tmp/output.pdf')\n\""}}
```

### 方法 2: 使用 exec 工具 + fpdf2（轻量级，支持中文）

```tool
{"tool": "exec", "params": {"cmd": "pip install fpdf2 -q && python3 << 'PYEOF'\nfrom fpdf import FPDF\npdf = FPDF()\npdf.add_page()\npdf.set_font('Helvetica', size=16)\npdf.cell(text='Title Here', new_x='LMARGIN', new_y='NEXT')\npdf.set_font('Helvetica', size=12)\npdf.multi_cell(w=0, text='Body content here...')\npdf.output('/tmp/output.pdf')\nprint('PDF created: /tmp/output.pdf')\nPYEOF"}}
```

### 方法 3: 使用 write_file + Markdown → PDF（如果安装了 md2pdf）

```tool
{"tool": "exec", "params": {"cmd": "pip install md2pdf -q && python3 -c \"from md2pdf.core import md2pdf; md2pdf('/tmp/output.pdf', md_content='# Title\\n\\nContent here')\""}}
```

## 发送文件给用户

创建文件后，**必须使用 `send_file` 工具**将文件发送到用户的聊天渠道（Telegram/Discord 等）：

```tool
{"tool": "send_file", "params": {"file_path": "/tmp/output.pdf", "caption": "这是您要求的PDF文件"}}
```

### 重要规则

1. **先创建文件，再发送**：确保文件路径存在且有效
2. **使用绝对路径**：始终使用 `/tmp/` 或其他绝对路径
3. **send_file 是唯一的文件发送方式**：不要尝试用 exec 运行 Python 代码来发送文件
4. **文件大小限制**：Telegram 最大 50MB
5. **caption 限制**：Telegram caption 最长 1024 字符

## 完整工作流示例

当用户要求创建并发送PDF时，按以下步骤执行：

**Step 1** — 创建 PDF 文件：
```tool
{"tool": "exec", "params": {"cmd": "python3 << 'EOF'\nfrom fpdf import FPDF\npdf = FPDF()\npdf.add_page()\npdf.set_font('Helvetica', 'B', 20)\npdf.cell(text='Report Title', new_x='LMARGIN', new_y='NEXT')\npdf.ln(10)\npdf.set_font('Helvetica', '', 12)\npdf.multi_cell(w=0, text='Report content goes here...')\npdf.output('/tmp/report.pdf')\nprint('OK')\nEOF"}}
```

**Step 2** — 发送到用户的聊天：
```tool
{"tool": "send_file", "params": {"file_path": "/tmp/report.pdf", "caption": "您的报告已生成"}}
```

## 注意事项

- 如果 send_file 返回错误 "No active channel session"，说明任务不是从 Telegram/渠道发起的，改为告知用户文件路径
- 中文内容需要注册中文字体（fpdf2 支持 `add_font` 方法加载 TTF 字体）
- 生成大型 PDF 时考虑分页
