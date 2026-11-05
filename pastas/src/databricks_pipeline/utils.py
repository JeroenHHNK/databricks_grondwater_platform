from pyspark.sql import DataFrame, SparkSession


def get_spark() -> SparkSession:
    return SparkSession.builder.getOrCreate()


def read_table(catalog: str, schema: str, table: str) -> DataFrame:
    spark = get_spark()
    return spark.table(f"{catalog}.{schema}.{table}")


def write_table(df: DataFrame, catalog: str, schema: str, table: str, mode: str = "overwrite") -> None:
    df.write.mode(mode).saveAsTable(f"{catalog}.{schema}.{table}")
