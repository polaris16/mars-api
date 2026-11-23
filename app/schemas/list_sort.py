"""[新增] 多个列表共用的显式排序参数。"""

from typing import Literal

CreatedAtSort = Literal["default", "created_at_asc", "created_at_desc"]
IdSort = Literal["default", "id_asc", "id_desc"]
NameSort = Literal["default", "name_asc", "name_desc"]
