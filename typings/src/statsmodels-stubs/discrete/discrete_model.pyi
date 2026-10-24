from collections.abc import Callable

import numpy as np
from numpy.typing import ArrayLike, NDArray

class BinaryResultsWrapper:
    params: NDArray[np.float64]

class Logit:
    def __init__(
        self,
        endog: NDArray[np.float64],
        exog: NDArray[np.float64],
        offset: ArrayLike | None = None,
        check_rank: bool = True,
        **kwargs: object,
    ) -> None: ...
    def fit(
        self,
        start_params: ArrayLike | None = None,
        method: str = 'newton',
        maxiter: int = 35,
        full_output: int = 1,
        disp: int = 1,
        callback: Callable[[NDArray[np.float64]], None] | None = None,
        **kwargs: object,
    ) -> BinaryResultsWrapper: ...
