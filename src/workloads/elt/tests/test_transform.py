"""Unit tests for transform.py."""

from __future__ import annotations

import pandas as pd
import pytest
from pandas.api.types import is_integer_dtype
from src.transform import (
    FEATURE_COLUMNS,
    NUMERIC_COLUMNS,
    RAW_REQUIRED_COLUMNS,
    TARGET_COLUMN,
    coerce_numeric_columns,
    drop_rows_with_missing_required_values,
    encode_target,
    engineer_features,
    normalize_column_names,
    standardize_string_values,
    transform,
    validate_transformed,
)


class TestNormalizeColumnNames:
    def test_lowercase_and_replace(self) -> None:
        dataframe = pd.DataFrame(columns=["Capital.Gain", "hours-per-week", "Native.Country"])
        result = normalize_column_names(dataframe)
        assert list(result.columns) == ["capital_gain", "hours_per_week", "native_country"]

    def test_already_normalized_stays_same(self, transformed_clean_df: pd.DataFrame) -> None:
        result = normalize_column_names(transformed_clean_df)
        assert list(result.columns) == list(transformed_clean_df.columns)

    def test_raises_on_duplicate_normalized_names(self) -> None:
        dataframe = pd.DataFrame(columns=["education.num", "education_num"])
        with pytest.raises(ValueError, match="duplicates"):
            normalize_column_names(dataframe)


class TestStandardizeStringValues:
    def test_trims_text_and_converts_placeholders(self) -> None:
        dataframe = pd.DataFrame({"workclass": [" Private ", "?", "None", "null"]})
        result = standardize_string_values(dataframe)
        assert result["workclass"].isna().tolist() == [False, True, True, True]
        na_mask = result["workclass"].isna()
        assert bool(na_mask.iloc[0]) is False
        assert bool(na_mask.iloc[1]) is True
        assert bool(na_mask.iloc[2]) is True
        assert bool(na_mask.iloc[3]) is True


class TestDropRowsWithMissingRequiredValues:
    def test_removes_null_target_rows(self, sample_raw_df: pd.DataFrame) -> None:
        result = drop_rows_with_missing_required_values(normalize_column_names(sample_raw_df))
        assert len(result) == 3
        assert bool(result["income"].notna().all())

    def test_returns_copy(self, sample_raw_df: pd.DataFrame) -> None:
        normalized = normalize_column_names(sample_raw_df)
        result = drop_rows_with_missing_required_values(normalized)
        assert result is not normalized


class TestCoerceNumericColumns:
    def test_converts_expected_numeric_columns(self) -> None:
        dataframe = pd.DataFrame({column: ["1", "2"] for column in NUMERIC_COLUMNS})
        result = coerce_numeric_columns(dataframe)
        for column in NUMERIC_COLUMNS:
            assert is_integer_dtype(result[column])

    def test_raises_when_numeric_column_is_missing(self) -> None:
        dataframe = pd.DataFrame({"age": [1]})
        with pytest.raises(ValueError, match="Missing required numeric column"):
            coerce_numeric_columns(dataframe)


class TestEncodeTarget:
    def test_maps_correctly(self) -> None:
        dataframe = pd.DataFrame({"income": ["<=50K.", ">50K", "<=50K", ">50K."]})
        result = encode_target(dataframe)
        assert result["income"].tolist() == [0, 1, 0, 1]

    def test_raises_on_unexpected_value(self) -> None:
        dataframe = pd.DataFrame({"income": ["<=50K", "unknown"]})
        with pytest.raises(ValueError, match="Unexpected target values"):
            encode_target(dataframe)

    def test_output_is_int(self) -> None:
        dataframe = pd.DataFrame({"income": ["<=50K", ">50K"]})
        result = encode_target(dataframe)
        assert result["income"].dtype == "int64"


class TestEngineerFeatures:
    def test_creates_net_capital(self) -> None:
        dataframe = pd.DataFrame({"capital_gain": [100], "capital_loss": [30], "age": [35]})
        result = engineer_features(dataframe)
        assert "net_capital" in result.columns
        assert result["net_capital"].iloc[0] == 70

    def test_raises_when_capital_columns_missing(self) -> None:
        dataframe = pd.DataFrame({"age": [35]})
        with pytest.raises(ValueError, match="capital_gain and capital_loss"):
            engineer_features(dataframe)

    def test_creates_age_group(self) -> None:
        dataframe = pd.DataFrame({"capital_gain": [0], "capital_loss": [0], "age": [30]})
        result = engineer_features(dataframe)
        assert "age_group" in result.columns
        assert result["age_group"].iloc[0] == "adult"

    @pytest.mark.parametrize(
        "age,expected",
        [
            (24, "young"),
            (25, "adult"),
            (44, "adult"),
            (45, "middle_age"),
            (64, "middle_age"),
            (65, "senior"),
        ],
    )
    def test_age_group_boundaries(self, age: int, expected: str) -> None:
        dataframe = pd.DataFrame({"capital_gain": [0], "capital_loss": [0], "age": [age]})
        result = engineer_features(dataframe)
        assert result["age_group"].iloc[0] == expected

    def test_preserves_original_data(self) -> None:
        dataframe = pd.DataFrame(
            {"capital_gain": [1], "capital_loss": [0], "age": [30], "other": [5]}
        )
        result = engineer_features(dataframe)
        assert "other" in result.columns
        assert result["other"].iloc[0] == 5


class TestValidateTransformed:
    def test_passes_on_valid_data(self, transformed_clean_df: pd.DataFrame) -> None:
        validate_transformed(transformed_clean_df)

    def test_fails_on_non_binary_target(self, transformed_clean_df: pd.DataFrame) -> None:
        dataframe = transformed_clean_df.copy()
        dataframe["income"] = [0, 2, 0]
        with pytest.raises(AssertionError, match="only 0 and 1"):
            validate_transformed(dataframe)

    def test_fails_on_nulls(self, transformed_clean_df: pd.DataFrame) -> None:
        dataframe = transformed_clean_df.copy()
        dataframe.loc[0, "age"] = None  # type: ignore[index]
        with pytest.raises(AssertionError, match="Null values"):
            validate_transformed(dataframe)

    def test_fails_on_missing_columns(self, transformed_clean_df: pd.DataFrame) -> None:
        dataframe = transformed_clean_df.drop(columns=["age_group"])
        with pytest.raises(AssertionError, match="Missing required columns"):
            validate_transformed(dataframe)

    def test_fails_on_empty_dataframe(self) -> None:
        dataframe = pd.DataFrame(columns=list(RAW_REQUIRED_COLUMNS) + list(FEATURE_COLUMNS))
        with pytest.raises(AssertionError, match="empty"):
            validate_transformed(dataframe)


class TestTransformPipeline:
    def test_end_to_end(self, sample_raw_df: pd.DataFrame) -> None:
        result = transform(sample_raw_df)
        assert len(result) == 3
        assert set(result[TARGET_COLUMN].unique()) == {0, 1}
        assert "net_capital" in result.columns
        assert "age_group" in result.columns
        assert not result.isnull().any().any()

    def test_all_numeric_columns_are_integer_dtype(self, sample_raw_df: pd.DataFrame) -> None:
        result = transform(sample_raw_df)
        for column in NUMERIC_COLUMNS:
            assert is_integer_dtype(result[column])
