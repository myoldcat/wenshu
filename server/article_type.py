"""Revision-owned article purpose, independent of topics and truth verification."""
from __future__ import annotations

ARTICLE_TYPE_POLICY_VERSION = 'article-purpose-v1'
ARTICLE_TYPES = ('fact', 'opinion', 'technical')
ARTICLE_TYPE_LABELS = {'fact': '事实类', 'opinion': '观点类', 'technical': '技术类'}
from .prompt_task_defaults import ARTICLE_TYPE_INSTRUCTIONS


class ArticleTypeRevisionConflict(ValueError):
    """The reader's immutable source revision is no longer the current one."""


def validate_article_type(value):
    if value is not None and (not isinstance(value, str) or value not in ARTICLE_TYPES):
        raise ValueError('文章性质只允许事实类、观点类、技术类或未标注')
    return value


def classification_article_type(value, *, required=False):
    if required and 'articleType' not in value:
        raise ValueError('分类结果缺少 articleType 文章性质字段；不能用主题或语言标签代替')
    return validate_article_type(value.get('articleType'))


def article_type_label(value):
    return ARTICLE_TYPE_LABELS.get(value)
