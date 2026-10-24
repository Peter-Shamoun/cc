con = duckdb.connect()                       # fresh, in-memory
con.execute(f"""
  CREATE SECRET (TYPE s3, KEY_ID '{c.key}', SECRET '{c.secret}',
                 SESSION_TOKEN '{c.token}', REGION 'us-west-2',
                 SCOPE 's3://claimclarity-prod/customer_b/')
""")                                         # Role B creds from STS
ver = read_s3_text("customer_b/semantic_layer/CURRENT")
base = f"s3://claimclarity-prod/customer_b/semantic_layer/{ver}"
for t in SEM_TABLES:                         # 10 names
    con.execute(f"CREATE VIEW sem.{t} AS "
                f"SELECT * FROM read_parquet('{base}/sem.{t}/*.parquet')")
con.execute("SET lock_configuration = true") # agent SQL can't change settings
