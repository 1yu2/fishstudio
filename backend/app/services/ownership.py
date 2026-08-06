"""项目归属校验单一入口（review 0.4：无主 SeriesProject 任意可访问整改）。

所有 Router/Service 的散装 `project.user_id != user.id` 判断统一收拢到
check_project_access，避免 user_id=NULL 的历史无主项目被任意用户访问。
"""

from typing import Optional

from app.db.models import SeriesProject, User


def check_project_access(
    project: SeriesProject,
    user: Optional[User] = None,
    *,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> None:
    """校验用户是否可访问 project；无权抛 PermissionError。

    - project.user_id is None（历史无主项目）：仅 admin 可访问（便于认领），
      其余一律拒绝（"历史无主项目，需管理员认领"）；
    - project.user_id 不匹配且非 admin：拒绝；
    - user/user_id 均为 None（内部无用户上下文调用）：只做无主校验。
    """
    if user is not None:
        user_id = user.id
        is_admin = bool(getattr(user, "is_admin", False))
    if project.user_id is None:
        if not is_admin:
            raise PermissionError("历史无主项目，需管理员认领")
        return
    if user_id is not None and project.user_id != user_id and not is_admin:
        raise PermissionError("project belongs to another user")
