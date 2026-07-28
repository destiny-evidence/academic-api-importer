# import asyncio
#
# import typer
#
# from academic_apis.broker.sqlite_broker import SQLiteBroker
#
# # from academic_apis.db import AccessToken, db_session
# from academic_apis.utils.types import Wrapper
# from academic_apis.wrappers import run_query
# from academic_apis.wrappers.scopus import ScopusWrapper
#
# app = typer.Typer()
#
#
# async def main() -> None:
#     broker = SQLiteBroker(queue_name="scopus")
#
#     # Never forget to call startup in the beginning.
#     await broker.startup()
#
#     # Register a task method
#     run_query_task = broker.task(run_query)
#
#     # Send the task to the broker.
#     task = await run_query_task.kiq(query="climate AND health", wrapper=Wrapper.SCOPUS)
#
#     # Wait for the result.
#     result = await task.wait_result(timeout=2)
#
#     print(f"Task execution took: {result.execution_time} seconds.")
#     if not result.is_err:
#         print(f"Returned value: {result.return_value}")
#     else:
#         print("Error found while executing task.")
#     await broker.shutdown()
#
#
# if __name__ == "__main__":
#     for qi, subquery in enumerate(ScopusWrapper.generate_subqueries("test")):
#         print(qi + 1, subquery)
#
#     asyncio.run(main())
