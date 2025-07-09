import os
import cx_Oracle
import pandas as pd
from tabulate import tabulate

class OracleConnectionManager:
    def __init__(self):
        self._connections = {
            "odw": {
                "user": os.getenv("DB_USER_ODW", "rptguser"),
                "password": os.getenv("DB_PASSWORD_ODW", "allusers"),
                "dsn": "odw"
            }
        }

    def get_connection(self, name):
        if name not in self._connections:
            raise ValueError(f"Unknown DB connection name: {name}")
        config = self._connections[name]
        return cx_Oracle.connect(
            user=config['user'],
            password=config['password'],
            dsn=config['dsn']
        )

# Define the SQL query
SQL_QUERY = """
WITH t AS (
    SELECT
        cd.cmpl_nme,
        cd.cmpl_fac_id,
        cd.well_api_nbr,
        engr_strg_nme,
        MAX(cf.eftv_dttm) AS last_inj_dte
    FROM
        cmpl_dmn cd
        JOIN cmpl_mnly_fact cf ON cd.cmpl_fac_id = cf.cmpl_fac_id
    WHERE
        actv_indc = 'Y'
        AND engr_strg_nme IN ('EP')
        AND prim_purp_type_cde = 'INJ'
        AND cmpl_state_type_cde = 'OPNL'
    GROUP BY
        cd.cmpl_nme, cd.cmpl_fac_id, cd.well_api_nbr, engr_strg_nme
),
t1 AS (
    SELECT
        t.cmpl_nme,
        t.cmpl_fac_id,
        t.well_api_nbr,
        t.engr_strg_nme,
        t.last_inj_dte,
        opg.top_perf,
        opg.btm_perf,
        opg.wlbr_fac_id
    FROM
        t
        JOIN curr_top_btm_actl_wlbr_opg opg ON t.cmpl_fac_id = opg.cmpl_fac_id
),
t2 AS (
    SELECT
        t1.cmpl_nme,
        t1.wlbr_fac_id,
        t1.cmpl_fac_id,
        t1.well_api_nbr,
        t1.engr_strg_nme,
        t1.last_inj_dte,
        wp.mrkr_nme,
        wp.md_qty AS mrkr_md_qty,
        t1.top_perf,
        t1.btm_perf,
        wp.mrkr_nme || '_' || 'BETWEEN_PERFS' AS in_between,
        wp.mrkr_nme || '_' || 'ABOVE_PERFS' AS above,
        wp.mrkr_nme || '_' || 'BELOW_PERFS' AS below
    FROM
        t1
        LEFT JOIN dwrptg.wlbr_mrkr_pick_dmn wp ON t1.wlbr_fac_id = wp.wlbr_fac_id
)
SELECT
    well_api_nbr,
    cmpl_nme,
    engr_strg_nme,
    last_inj_dte,
    ROUND(top_perf, 0) AS top_perf,
    ROUND(btm_perf, 0) AS btm_perf,
    CASE
        WHEN mrkr_md_qty < top_perf THEN above
        WHEN mrkr_md_qty > btm_perf THEN below
        WHEN mrkr_md_qty BETWEEN top_perf AND btm_perf THEN in_between
        ELSE NULL
    END AS location_test
FROM
    t2
ORDER BY
    engr_strg_nme,
    location_test
"""

def run_query():
    try:
        conn = OracleConnectionManager().get_connection("odw")
        df = pd.read_sql(SQL_QUERY, con=conn)
        print(tabulate(df, headers="keys", tablefmt="psql", showindex=False))
        conn.close()
    except Exception as e:
        print("Error occurred:", e)

if __name__ == "__main__":
    run_query()
