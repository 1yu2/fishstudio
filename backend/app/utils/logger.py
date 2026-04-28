"""
日志配置模块
统一配置应用的日志输出，支持同时输出到控制台和文件
"""
import logging
import logging.handlers
import os
from pathlib import Path
from datetime import datetime


def setup_logging(log_level: str = "INFO", log_dir: str = None):
    """
    配置应用的日志系统
    
    Args:
        log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: 日志文件目录，如果为 None 则使用 backend/logs
    """
    # 确定日志目录
    if log_dir is None:
        BASE_DIR = Path(__file__).parent.parent.parent
        log_dir = BASE_DIR / "logs"
    else:
        log_dir = Path(log_dir)
    
    # 确保日志目录存在
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 日志文件名：使用日期作为文件名
    log_file = log_dir / f"fishstudio_{datetime.now().strftime('%Y%m%d')}.log"
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # 清除已有的处理器（避免重复配置）
    root_logger.handlers.clear()
    
    # 日志格式
    detailed_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    simple_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    # 1. 控制台处理器 - 使用简单格式
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_formatter = logging.Formatter(simple_format, datefmt='%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # 2. 文件处理器 - 使用详细格式，支持按大小轮转
    # 每个文件最大 10MB，保留 5 个备份文件
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # 文件记录更详细的日志
    file_formatter = logging.Formatter(detailed_format, datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    # 3. 错误日志单独记录到文件
    error_log_file = log_dir / f"fishstudio_error_{datetime.now().strftime('%Y%m%d')}.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)  # 只记录 ERROR 及以上级别
    error_handler.setFormatter(file_formatter)
    root_logger.addHandler(error_handler)
    
    # 配置第三方库的日志级别（避免过多噪音）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    # 记录日志配置完成
    logger = logging.getLogger(__name__)
    logger.info(f"✅ 日志系统已配置: 级别={log_level}, 日志目录={log_dir}")
    logger.info(f"   控制台输出: 启用")
    logger.info(f"   日志文件: {log_file}")
    logger.info(f"   错误日志: {error_log_file}")


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器
    
    Args:
        name: 日志记录器名称，通常使用 __name__
    
    Returns:
        logging.Logger 实例
    """
    return logging.getLogger(name)
