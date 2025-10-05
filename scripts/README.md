# 快速开始：历史数据迁移

## 一键运行迁移 🚀

```bash
# 进入项目目录
cd /Users/zhaoyu/Projects/JakartaBackend

# 运行迁移（会提示确认）
python scripts/simple_migrate_update_count.py
```

就这么简单！脚本会：
1. ✅ 自动检查数据库
2. ✅ 计算每个 DN 的记录数
3. ✅ 显示将要更新的统计信息
4. ⚠️  **询问确认** - 输入 `yes` 继续
5. ✅ 执行更新
6. ✅ 自动验证结果

## 想先看看会改什么？

使用高级脚本的 dry-run 模式：

```bash
# 只预览，不修改数据
python scripts/migrate_update_count.py --dry-run
```

## 如果出现问题

1. **确保环境变量已设置**
   ```bash
   echo $DATABASE_URL  # 应该显示数据库连接字符串
   ```

2. **使用虚拟环境**
   ```bash
   source .venv/bin/activate
   ```

3. **查看详细错误信息**
   已包含在脚本输出中

## 更多信息

详细文档：`docs/MIGRATION_UPDATE_COUNT.md`

## 已提供的迁移工具

| 文件 | 用途 |
|------|------|
| `scripts/simple_migrate_update_count.py` | **推荐** - 简单交互式脚本 |
| `scripts/migrate_update_count.py` | 高级选项（dry-run、verbose） |
| `scripts/migrate_update_count.sql` | SQL 脚本（直接数据库操作） |

