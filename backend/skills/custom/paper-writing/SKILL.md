---
name: paper-writing
description: 论文写作专家。专注于提供从选题、文献综述、结构规划到最终排版的全流程论文写作支持。适用于学术论文、学位论文、研究报告等各类学术写作场景。
---

# Paper Writing

## Overview

本技能提供论文写作全流程支持，从选题、文献综述、结构规划到最终排版，帮助用户高效完成学术论文、学位论文和研究报告的写作。

## Workflow Decision Tree

1. **用户需求分析**：理解用户的论文类型、主题、字数要求和截止日期
2. **选题与研究方向确定**：帮助用户确定合适的研究主题和方向
3. **文献综述**：指导用户进行文献检索和综述撰写
4. **论文结构规划**：提供标准论文结构模板和个性化调整建议
5. **内容撰写**：提供各部分内容的写作指导和示例
6. **引用与参考文献**：指导正确的引用格式和参考文献管理
7. **论文修改与润色**：提供语言润色和结构优化建议
8. **最终排版**：指导论文格式调整和排版规范

## Core Capabilities

### 1. 选题与研究方向
- 提供研究主题建议
- 帮助用户缩小研究范围
- 评估研究可行性

### 2. 文献综述
- 指导文献检索策略
- 提供文献管理工具建议
- 帮助组织文献综述结构

### 3. 论文结构规划
- 提供标准论文结构模板
- 帮助用户定制个性化结构
- 提供各章节内容建议

### 4. 内容撰写指导
- 提供各部分写作技巧
- 提供学术写作规范指导
- 提供常见问题解决方案

### 5. 引用与参考文献
- 指导不同引用格式（APA, MLA, Chicago等）
- 提供参考文献管理工具建议
- 帮助用户正确引用文献

### 6. 论文修改与润色
- 提供语言润色建议
- 帮助优化论文结构
- 提供内容逻辑检查

### 7. 最终排版
- 指导论文格式调整
- 提供排版规范建议
- 帮助用户准备最终提交版本

## Resources

This skill includes example resource directories that demonstrate how to organize different types of bundled resources:

### scripts/
Executable code (Python/Bash/etc.) that can be run directly to perform specific operations.

**Examples from other skills:**
- PDF skill: `fill_fillable_fields.py`, `extract_form_field_info.py` - utilities for PDF manipulation
- DOCX skill: `document.py`, `utilities.py` - Python modules for document processing

**Appropriate for:** Python scripts, shell scripts, or any executable code that performs automation, data processing, or specific operations.

**Note:** Scripts may be executed without loading into context, but can still be read by FishStudio Agent for patching or environment adjustments.

### references/
Documentation and reference material intended to be loaded into context to inform FishStudio Agent's process and thinking.

**Examples from other skills:**
- Product management: `communication.md`, `context_building.md` - detailed workflow guides
- BigQuery: API reference documentation and query examples
- Finance: Schema documentation, company policies

**Appropriate for:** In-depth documentation, API references, database schemas, comprehensive guides, or any detailed information that FishStudio Agent should reference while working.

### assets/
Files not intended to be loaded into context, but rather used within the output FishStudio Agent produces.

**Examples from other skills:**
- Brand styling: PowerPoint template files (.pptx), logo files
- Frontend builder: HTML/React boilerplate project directories
- Typography: Font files (.ttf, .woff2)

**Appropriate for:** Templates, boilerplate code, document templates, images, icons, fonts, or any files meant to be copied or used in the final output.

---

**Any unneeded directories can be deleted.** Not every skill requires all three types of resources.