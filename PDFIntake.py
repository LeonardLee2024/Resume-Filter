import pymupdf

doc = pymupdf.open(r"C:\Users\leona\Downloads\leonard.lee.activity5.3.1.pdf")
page = doc[0]
text = page.get_text()
print(f"text: {text}")                                              