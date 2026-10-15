# Purpose of the project
APIs are not good at handling large result sets
often fail mid-way, have to restart
solution: split up into subtasks so re-starting isn't as "costly"

this tool provides an easy-to-use user interface to submit large queries and, once done, download complete set

features
* manage Scholarly API tokens
* support for scopus (planned support for pubmed, dimensions.ai, OpenAlex, Web of Science)
* automatic retry on errors
* test queries (check if query works in scholarly API before submission)
* search grammar check (pinpoints syntax errors; APIs typically only respond with generic errors)
* search query formatting (format query one term per line and indentation for subqueries)
* submit custom sub-queries via backend API