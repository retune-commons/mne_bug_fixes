# Author: Jean-Remi King, <jeanremi.king@gmail.com>
#
# License: BSD-3-Clause
# Copyright the MNE-Python contributors.

import platform
from inspect import signature

import numpy as np
import pytest
from numpy.testing import assert_array_equal, assert_equal

from mne.decoding.search_light import GeneralizingEstimator, SlidingEstimator
from mne.decoding.transformer import Vectorizer
from mne.utils import _record_warnings, check_version, use_log_level

pytest.importorskip("sklearn")


def make_data():
    """Make data."""
    n_epochs, n_chan, n_time = 50, 32, 10
    X = np.random.rand(n_epochs, n_chan, n_time)
    y = np.arange(n_epochs) % 2
    for ii in range(n_time):
        coef = np.random.randn(n_chan)
        X[y == 0, :, ii] += coef
        X[y == 1, :, ii] -= coef
    return X, y


def test_search_light():
    """Test SlidingEstimator."""
    # https://github.com/scikit-learn/scikit-learn/issues/27711
    if platform.system() == "Windows" and check_version("numpy", "2.0.0.dev0"):
        pytest.skip("sklearn int_t / long long mismatch")
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.metrics import make_scorer, roc_auc_score
    from sklearn.pipeline import make_pipeline

    with _record_warnings():  # NumPy module import
        from sklearn.ensemble import BaggingClassifier
    from sklearn.base import is_classifier

    logreg = LogisticRegression(solver="liblinear", multi_class="ovr", random_state=0)

    X, y = make_data()
    n_epochs, _, n_time = X.shape
    # init
    pytest.raises(ValueError, SlidingEstimator, "foo")
    sl = SlidingEstimator(Ridge())
    assert not is_classifier(sl)
    sl = SlidingEstimator(LogisticRegression(solver="liblinear"))
    assert is_classifier(sl)
    # fit
    assert_equal(sl.__repr__()[:18], "<SlidingEstimator(")
    sl.fit(X, y)
    assert_equal(sl.__repr__()[-28:], ", fitted with 10 estimators>")
    pytest.raises(ValueError, sl.fit, X[1:], y)
    pytest.raises(ValueError, sl.fit, X[:, :, 0], y)
    sl.fit(X, y, sample_weight=np.ones_like(y))

    # transforms
    pytest.raises(ValueError, sl.predict, X[:, :, :2])
    y_trans = sl.transform(X)
    assert X.dtype == y_trans.dtype == np.dtype(float)
    y_pred = sl.predict(X)
    assert y_pred.dtype == np.dtype(int)
    assert_array_equal(y_pred.shape, [n_epochs, n_time])
    y_proba = sl.predict_proba(X)
    assert y_proba.dtype == np.dtype(float)
    assert_array_equal(y_proba.shape, [n_epochs, n_time, 2])

    # score
    score = sl.score(X, y)
    assert_array_equal(score.shape, [n_time])
    assert np.sum(np.abs(score)) != 0
    assert score.dtype == np.dtype(float)

    sl = SlidingEstimator(logreg)
    assert_equal(sl.scoring, None)

    # Scoring method
    for scoring in ["foo", 999]:
        sl = SlidingEstimator(logreg, scoring=scoring)
        sl.fit(X, y)
        pytest.raises((ValueError, TypeError), sl.score, X, y)

    # Check sklearn's roc_auc fix: scikit-learn/scikit-learn#6874
    # -- 3 class problem
    sl = SlidingEstimator(logreg, scoring="roc_auc")
    y = np.arange(len(X)) % 3
    sl.fit(X, y)
    with pytest.raises(ValueError, match="for two-class"):
        sl.score(X, y)
    # But check that valid ones should work with new enough sklearn
    if "multi_class" in signature(roc_auc_score).parameters:
        scoring = make_scorer(roc_auc_score, needs_proba=True, multi_class="ovo")
        sl = SlidingEstimator(logreg, scoring=scoring)
        sl.fit(X, y)
        sl.score(X, y)  # smoke test

    # -- 2 class problem not in [0, 1]
    y = np.arange(len(X)) % 2 + 1
    sl.fit(X, y)
    score = sl.score(X, y)
    assert_array_equal(
        score,
        [roc_auc_score(y - 1, _y_pred - 1) for _y_pred in sl.decision_function(X).T],
    )
    y = np.arange(len(X)) % 2

    # Cannot pass a metric as a scoring parameter
    sl1 = SlidingEstimator(logreg, scoring=roc_auc_score)
    sl1.fit(X, y)
    pytest.raises(ValueError, sl1.score, X, y)

    # Now use string as scoring
    sl1 = SlidingEstimator(logreg, scoring="roc_auc")
    sl1.fit(X, y)
    rng = np.random.RandomState(0)
    X = rng.randn(*X.shape)  # randomize X to avoid AUCs in [0, 1]
    score_sl = sl1.score(X, y)
    assert_array_equal(score_sl.shape, [n_time])
    assert score_sl.dtype == np.dtype(float)

    # Check that scoring was applied adequately
    scoring = make_scorer(roc_auc_score, needs_threshold=True)
    score_manual = [
        scoring(est, x, y) for est, x in zip(sl1.estimators_, X.transpose(2, 0, 1))
    ]
    assert_array_equal(score_manual, score_sl)

    # n_jobs
    sl = SlidingEstimator(logreg, n_jobs=None, scoring="roc_auc")
    score_1job = sl.fit(X, y).score(X, y)
    sl.n_jobs = 2
    score_njobs = sl.fit(X, y).score(X, y)
    assert_array_equal(score_1job, score_njobs)
    sl.predict(X)

    # n_jobs > n_estimators
    sl.fit(X[..., [0]], y)
    sl.predict(X[..., [0]])

    # pipeline
    class _LogRegTransformer(LogisticRegression):
        def transform(self, X):
            return super(_LogRegTransformer, self).predict_proba(X)[..., 1]

    logreg_transformer = _LogRegTransformer(
        random_state=0, multi_class="ovr", solver="liblinear"
    )
    pipe = make_pipeline(SlidingEstimator(logreg_transformer), logreg)
    pipe.fit(X, y)
    pipe.predict(X)

    # n-dimensional feature space
    X = np.random.rand(10, 3, 4, 2)
    y = np.arange(10) % 2
    y_preds = list()
    for n_jobs in [1, 2]:
        pipe = SlidingEstimator(make_pipeline(Vectorizer(), logreg), n_jobs=n_jobs)
        y_preds.append(pipe.fit(X, y).predict(X))
        features_shape = pipe.estimators_[0].steps[0][1].features_shape_
        assert_array_equal(features_shape, [3, 4])
    assert_array_equal(y_preds[0], y_preds[1])

    # Bagging classifiers
    X = np.random.rand(10, 3, 4)
    for n_jobs in (1, 2):
        pipe = SlidingEstimator(BaggingClassifier(None, 2), n_jobs=n_jobs)
        pipe.fit(X, y)
        pipe.score(X, y)
        assert isinstance(pipe.estimators_[0], BaggingClassifier)


def test_generalization_light():
    """Test GeneralizingEstimator."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline

    logreg = LogisticRegression(solver="liblinear", multi_class="ovr", random_state=0)

    X, y = make_data()
    n_epochs, _, n_time = X.shape
    # fit
    gl = GeneralizingEstimator(logreg)
    assert_equal(repr(gl)[:23], "<GeneralizingEstimator(")
    gl.fit(X, y)
    gl.fit(X, y, sample_weight=np.ones_like(y))

    assert_equal(gl.__repr__()[-28:], ", fitted with 10 estimators>")
    # transforms
    y_pred = gl.predict(X)
    assert_array_equal(y_pred.shape, [n_epochs, n_time, n_time])
    assert y_pred.dtype == np.dtype(int)
    y_proba = gl.predict_proba(X)
    assert y_proba.dtype == np.dtype(float)
    assert_array_equal(y_proba.shape, [n_epochs, n_time, n_time, 2])

    # transform to different datasize
    y_pred = gl.predict(X[:, :, :2])
    assert_array_equal(y_pred.shape, [n_epochs, n_time, 2])

    # score
    score = gl.score(X[:, :, :3], y)
    assert_array_equal(score.shape, [n_time, 3])
    assert np.sum(np.abs(score)) != 0
    assert score.dtype == np.dtype(float)

    gl = GeneralizingEstimator(logreg, scoring="roc_auc")
    gl.fit(X, y)
    score = gl.score(X, y)
    auc = roc_auc_score(y, gl.estimators_[0].predict_proba(X[..., 0])[..., 1])
    assert_equal(score[0, 0], auc)

    for scoring in ["foo", 999]:
        gl = GeneralizingEstimator(logreg, scoring=scoring)
        gl.fit(X, y)
        pytest.raises((ValueError, TypeError), gl.score, X, y)

    # Check sklearn's roc_auc fix: scikit-learn/scikit-learn#6874
    # -- 3 class problem
    gl = GeneralizingEstimator(logreg, scoring="roc_auc")
    y = np.arange(len(X)) % 3
    gl.fit(X, y)
    pytest.raises(ValueError, gl.score, X, y)
    # -- 2 class problem not in [0, 1]
    y = np.arange(len(X)) % 2 + 1
    gl.fit(X, y)
    score = gl.score(X, y)
    manual_score = [
        [roc_auc_score(y - 1, _y_pred) for _y_pred in _y_preds]
        for _y_preds in gl.decision_function(X).transpose(1, 2, 0)
    ]
    assert_array_equal(score, manual_score)

    # n_jobs
    gl = GeneralizingEstimator(logreg, n_jobs=2)
    gl.fit(X, y)
    y_pred = gl.predict(X)
    assert_array_equal(y_pred.shape, [n_epochs, n_time, n_time])
    score = gl.score(X, y)
    assert_array_equal(score.shape, [n_time, n_time])

    # n_jobs > n_estimators
    gl.fit(X[..., [0]], y)
    gl.predict(X[..., [0]])

    # n-dimensional feature space
    X = np.random.rand(10, 3, 4, 2)
    y = np.arange(10) % 2
    y_preds = list()
    for n_jobs in [1, 2]:
        pipe = GeneralizingEstimator(make_pipeline(Vectorizer(), logreg), n_jobs=n_jobs)
        y_preds.append(pipe.fit(X, y).predict(X))
        features_shape = pipe.estimators_[0].steps[0][1].features_shape_
        assert_array_equal(features_shape, [3, 4])
    assert_array_equal(y_preds[0], y_preds[1])


@pytest.mark.parametrize(
    "n_jobs, verbose", [(1, False), (2, False), (1, True), (2, "info")]
)
def test_verbose_arg(capsys, n_jobs, verbose):
    """Test controlling output with the ``verbose`` argument."""
    from sklearn.svm import SVC

    X, y = make_data()
    clf = SVC()

    # shows progress bar and prints other messages to the console
    with use_log_level(True):
        for estimator_object in [SlidingEstimator, GeneralizingEstimator]:
            estimator = estimator_object(clf, n_jobs=n_jobs, verbose=verbose)
            estimator = estimator.fit(X, y)
            estimator.score(X, y)
            estimator.predict(X)

            stdout, stderr = capsys.readouterr()
            if isinstance(verbose, bool) and not verbose:
                assert all(channel == "" for channel in (stdout, stderr))
            else:
                assert any(len(channel) > 0 for channel in (stdout, stderr))


def test_cross_val_predict():
    """Test cross_val_predict with predict_proba."""
    from sklearn.base import BaseEstimator, clone
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import cross_val_predict

    rng = np.random.RandomState(42)
    X = rng.randn(10, 1, 3)
    y = rng.randint(0, 2, 10)

    estimator = SlidingEstimator(LinearRegression())
    cross_val_predict(estimator, X, y, cv=2)

    class Classifier(BaseEstimator):
        """Moch class that does not have classes_ attribute."""

        def __init__(self):
            self.base_estimator = LinearDiscriminantAnalysis()

        def fit(self, X, y):
            self.estimator_ = clone(self.base_estimator).fit(X, y)
            return self

        def predict_proba(self, X):
            return self.estimator_.predict_proba(X)

    with pytest.raises(AttributeError, match="classes_ attribute"):
        estimator = SlidingEstimator(Classifier())
        cross_val_predict(estimator, X, y, method="predict_proba", cv=2)

    estimator = SlidingEstimator(LinearDiscriminantAnalysis())
    cross_val_predict(estimator, X, y, method="predict_proba", cv=2)


@pytest.mark.slowtest
def test_sklearn_compliance():
    """Test LinearModel compliance with sklearn."""
    pytest.importorskip("sklearn")
    from sklearn.linear_model import LogisticRegression
    from sklearn.utils.estimator_checks import check_estimator

    est = SlidingEstimator(LogisticRegression(), allow_2d=True)

    ignores = (
        "check_estimator_sparse_data",  # we densify
        "check_classifiers_one_label_sample_weights",  # don't handle singleton
        "check_classifiers_classes",  # dim mismatch
        "check_classifiers_train",
        "check_decision_proba_consistency",
        "check_parameters_default_constructible",
    )
    for est, check in check_estimator(est, generate_only=True):
        if any(ignore in str(check) for ignore in ignores):
            continue
        check(est)
