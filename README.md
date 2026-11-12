# banking-project

# Postgres Account & Domain:

- **Username:** `*********`
- **Password:** `*********`
- **Domain:** [http://35.190.163.200:8111/](http://35.190.163.200:8111/)

## Proposal

Our proposal was this:

- We wanted to create a banking/investment database that essentially emulates personal banking with some simple investing features. At a basic level, the database will be able to store users bankings accounts and details about the person like their user_id, name, DOB, email, address. The banking account will have the simple functions of a standard checking account like storing money, being able to withdraw and deposit money. The account will also be able to transfer money to accounts of other users. The user will be able to see this data through a front-end that we are thinking of creating with React that will essentially be a user interface for a basic online banking system.
  Bank accounts can also invest in stocks and we could retrieve stock data in real time by using an API and store in the database how much the user invested and how much he/she is gaining/losing based on the stock portfolio’s current value.

## What We Implemented

- We were able to implement the majority of the personal banking features. Each user has a profile. a bank account, and a stock portfolio. The profile holds all the information listed above, and the bank account is able to transfer money to other bank accounts and purchase stocks. We did implement

## What We Did Not Implement

- One thing we did not implement was withdrawals and deposits. Of course are not able to provide real money, but our database has tables for withdrawals and deposits between the bank account and the stock portfolio. We did not implement this because we figured it would be easier and more convenient for portfolio transactions to occur straight from the bank account rather than having a separate account for the portfolio. Finally, we did not yet implement live stock price updates. Even though this would be relatively easy to accomplish (update the stock data in the database with the yfinance API), this would still take too much time for finance APIs, which are not direclty applicable to this course. We focused more on pages and features directly applicable to SQL, Postgres, Flask, and Jinja.

## Key Pages

- The 2 most interesting pages are likely the stocks/transactions page (these are very similar, so we will choose the transactions page for this discussion), and the account creation page.

### Transactions Page

- The transaction page is interesting because it requires many database operations. Namely, it requires subtracting money from the current bank account, using the entered account number to find the recipient's bank account and add money to its balance. We must also insert the bank transaction into the bank transaction table and ensure that it appears on the transaction pages of both accounts. Keeping the list of transactions updated was one of the more involved uses of Flask and Ninja in this project. This page is also central to what bank accounts do

### Account Creation Page

- The account creation page is interesting because it likely requires the most SQL and error checking/field validation. When the new user enters all their information, which involves about a dozen fields, we have to check that all fields are valid, properly implement error checks, and execute half a dozen long insertion queries. For example, we need to attempt to select a profile with an identical username to ensure that the username has not yet been taken. After validation, we insert new rows into the user profile, bank account, and portfolio tables as well as all the relation tables that connect the profile, account, and portfolio. This page especially relies on the principle of atomicity. If one of the transactions fails, then the user's data will not work with the other pages, so all the transactions get rolled back.

## AI Disclaimer

Generative AI was used for generating "templates" for the UI of each webpage. Each prompt was formatted as such:

"Maintaining the current UI, generate a xxxxx.html page (where xxxxx is portfolio, bank account, profile, etc.) which displays the following information for a user: {copied column headers for the table corresponding to the page}"
# basic_vol_curve
