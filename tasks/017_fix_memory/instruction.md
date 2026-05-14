# fix memory leak in indicators
rolling calculations create many intermediate objects
use inplace operations where possible
check for large dataframe copies
