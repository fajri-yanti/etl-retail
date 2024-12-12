from airflow import DAG
from airflow.operators.python_operator import PythonOperator
from airflow.hooks.postgres_hook import PostgresHook
# from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import datetime
import pandas as pd

def extract_data(ti):
    postgres_hook = PostgresHook(postgres_conn_id='dbmaster')
    query = 'SELECT * FROM transaction_data'
    connection = postgres_hook.get_conn()
    cursor = connection.cursor()
    cursor.execute(query)

    result = cursor.fetchall()
    column_name = [desc[0] for desc in cursor.description]
    df = pd.DataFrame(result, columns=column_name)

    ti.xcom_push(key='transaction_data', value=df.to_json())

def transform_data(ti):
    df_json = ti.xcom_pull(key='transaction_data', task_ids='extract_task')
    data = pd.read_json(df_json)
    data['total_transaction'] = data['quantity'] * data['unitprice']
    df = data[['country', 'stockcode', 'description','quantity','total_transaction']]
    summary_df = df.groupby(by=['country', 'stockcode', 'description']).sum().reset_index()
    ti.xcom_push(key='transaction_groupby', value=summary_df.to_json())

def load_data(ti):
    df_json = ti.xcom_pull(key='transaction_groupby', task_ids='transform_task')
    data = pd.read_json(df_json)
    postgres_hook = PostgresHook(postgres_conn_id='dbwarehouse')
    connection = postgres_hook.get_conn()

    try:
        cur = connection.cursor()

        cur.execute("""

            CREATE TABLE IF NOT EXISTS transaction_groupby(
                    country VARCHAR(100),
                    stockcode VARCHAR(100),
                    description VARCHAR(200),
                    quantity INT,
                    total_transaction FLOAT
                    );
            """
        )

        cur.execute("DELETE FROM transaction_groupby")
        connection.commit()

        engine = postgres_hook.get_sqlalchemy_engine()
        data.to_sql('transaction_groupby', con=engine, if_exists='append', index=False)

    except Exception as e:
        print(f"Error:{e}")
        connection.rollback()

    finally:
        cur.close()
        connection.close()

default_args = {
    'Owner' : 'fajri',
    'start_date' : datetime(2024,12,12),
    'retries' : 1
}


dag = DAG(
    'summary_transaction',
    default_args = default_args,
    description = 'simple DAG for summary transaction',
    schedule = '@daily',
    max_active_runs=1,
    catchup=False
)

extract_task = PythonOperator(
    task_id = 'extract_task',
    python_callable = extract_data,
    dag = dag
)

transform_task = PythonOperator(
    task_id = 'transform_task',
    python_callable = transform_data,
    dag = dag
)

load_task = PythonOperator(
    task_id = 'load_task',
    python_callable = load_data,
    dag = dag
)

extract_task >> transform_task >> load_task
