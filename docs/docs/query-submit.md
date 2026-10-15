# describe query utils view
* formatting
* testing

box: formatting very useful to identify errors, better readability, good in diff (e.g. git), ...

under the hood, we have a syntax grammar (link src/academic_apis/grammars/scopus.lark), should be correct.
future:
* hopefully enables us to translate between search providers (where possible)
* run query analysis to show which terms contribute to overall result

screenshot: query-utils-format.png
screenshot: query-utils-test.png

# describe query-submit view 

see screenshot: query-submit.png

```
for year in ...:
  for first author in aa* ... zz*:
    request: query + year + author filter
```

for year filter: three components: before start year, after end year (if set), and then in chunks of "years per step"
Looping over years is optional
Looping over first authors is optional

future: 
quasi sampling download (e.g. when you want to test query coverage

screenshot: query-submit.png