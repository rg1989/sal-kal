'use strict';
// Sal-Kal (סל קל, "light basket") in Hebrew (the default) and English. Strings are written in English in the code: t('Add to cart') looks the
// English text up in HE; {name} placeholders are filled from the second argument. Static labels in index.html carry
// data-i18n (text), data-i18n-ph (placeholder), data-i18n-aria (aria-label) and data-i18n-title (title).
// Hebrew also turns the page right-to-left (<html dir="rtl">); the CSS uses logical properties, so it mirrors.

let LANG = (() => { try { return localStorage.getItem('lang') === 'en' ? 'en' : 'he'; } catch { return 'he'; } })();
const L = () => LANG;
const rtl = () => LANG === 'he';
const locale = () => (LANG === 'he' ? 'he-IL' : 'en-GB');

const HE = {
  // ---- your places: around one, or on the way between two
  'Around a place': 'סביב מקום', 'On the way': 'בדרך', 'Around': 'סביב', 'Within': 'ברדיוס', 'From': 'מוצא', 'To': 'יעד',
  'Swap': 'החלפת כיוון', 'Off the way': 'סטייה', 'Add a place…': 'הוספת מקום…', 'Add a place': 'הוספת מקום',
  'Add a second place': 'הוספת מקום שני', 'Your places': 'המקומות שלך', 'Edit': 'עריכה', 'Name': 'שם',
  'Save place': 'שמירת המקום', 'Place saved': 'המקום נשמר', 'Where do you shop?': 'איפה קונים?',
  // ---- on my way: shopping along the route home -> work
  'Around home': 'ליד הבית', 'On my way': 'בדרך לעבודה', 'Where to look': 'איפה לחפש', 'Work': 'עבודה',
  'How you go': 'איך מגיעים', 'How far off the way': 'כמה לסטות מהדרך', 'By car': 'ברכב', 'By bike': 'באופניים',
  'On foot': 'ברגל', 'Work address saved': 'כתובת העבודה נשמרה', 'On the way to {place}': 'בדרך ל{place}',
  'on your way': 'בדרך', 'On your way': 'בדרך', '{d} off your way': '{d} מהדרך', 'Change': 'שינוי',
  'Biggest savings on your way': 'החיסכון הגדול בדרך', 'No stores on your way': 'אין חנויות בדרך',
  'No store on your way has these products.': 'אף חנות בדרך לא מוכרת את המוצרים האלה.',
  'Only branch on your way': 'הסניף היחיד בדרך', 'Stops on your way': 'עצירות בדרך', 'Direction': 'כיוון',
  'To {place}': 'אל {place}', 'Number of stops': 'מספר עצירות', '{price} less': '{price} פחות',
  'Not on your way': 'לא בדרך', 'All {n} products': 'כל {n} המוצרים', '{n} min in all': '{n} דק׳ בסך הכול',
  'no extra time': 'בלי זמן נוסף', 'Trip time unavailable right now': 'זמן הנסיעה לא זמין כרגע',
  // ---- units and counts
  '{n} m': '{n} מ׳', '{n} km': '{n} ק״מ', 'km': 'ק״מ', 'm': 'מ׳', '+{n} km': '+{n} ק״מ', '+{n} min': '+{n} דק׳', 'kg': 'ק״ג', '{n} kg': '{n} ק״ג',
  'Any': 'כל', 'distance': 'מרחק',
  // ---- brand and header
  'Sal-Kal': 'סל קל', 'Sal-Kal — home': 'סל קל — דף הבית', 'Cart': 'סל', 'Your account': 'החשבון שלך',
  'Search milk, eggs, a barcode…': 'חיפוש מוצרים — חלב, ביצים, ברקוד',
  'Search products (results appear as you type)': 'חיפוש מוצרים (התוצאות מופיעות תוך כדי הקלדה)',
  'Clear search': 'ניקוי החיפוש', 'Categories': 'קטגוריות', 'Stores near you': 'חנויות לידך',
  'Set your location': 'הגדרת מיקום', 'Within {n} km of {place}': 'עד {n} ק״מ מ{place}', 'Near {place}': 'ליד {place}',
  'Language': 'שפה', 'Theme': 'ערכת נושא', 'Theme: {name} (click to change)': 'ערכת נושא: {name} (לחיצה לשינוי)',
  'Match device': 'לפי המכשיר', 'Light': 'בהיר', 'Dark': 'כהה', 'Privacy': 'פרטיות',
  // ---- home
  'Recent:': 'אחרונים:', 'Shop by category': 'לפי קטגוריה', 'Biggest savings near you': 'החיסכון הגדול לידך',
  'Started this week': 'התחילו השבוע', 'Ending soon': 'נגמרים בקרוב', 'Recently viewed': 'נצפו לאחרונה',
  'Set your address': 'הגדירו כתובת', 'to see the stores around you': 'כדי לראות את החנויות סביבך',
  'within {n} km': 'ברדיוס {n} ק״מ', 'nearby': 'בסביבה', 'Branches on the map': 'סניפים במפה',
  'Cheapest per chain': 'הזול בכל רשת', '{branches} of {chains} {where}': '{branches} של {chains} {where}',
  'Home': 'בית', 'price the cart here': 'תמחור הסל כאן',
  // ---- product cards
  'Add': 'הוספה', 'Add {n}': 'הוספת {n}', 'Add to cart': 'הוספה לסל', 'One less': 'פחות אחד', 'One more': 'עוד אחד',
  'from': 'החל מ־', 'near you': 'קרוב אליך', 'price': 'מחיר', 'each, ×{n}': 'ליחידה, ×{n}', 'deal price': 'מחיר מבצע',
  'usually {price}': 'בדרך כלל {price}', 'Similar at {n}': 'דומה ב־{n}', '{chain} only': 'רק ב{chain}',
  'Cheapest {price} at {chain}': 'הכי זול {price} ב{chain}', '{price} at {chain}': '{price} ב{chain}',
  'ends today': 'נגמר היום', 'ends tomorrow': 'נגמר מחר', 'ends {day}': 'נגמר ב{day}',
  // ---- browse
  'Add a word to narrow down': 'הוספת מילה לסינון', 'On deal now': 'במבצע עכשיו', 'Sort': 'מיון',
  'Best match': 'הכי מתאים', 'Lowest price': 'המחיר הנמוך', 'Biggest saving': 'החיסכון הגדול',
  'Sold in most stores': 'נמכר בהכי הרבה חנויות', 'Remove {x}': 'הסרת {x}', 'Narrow down…': 'סינון נוסף…',
  'Type a product…': 'הקלידו מוצר…', 'Searching…': 'מחפשים…',
  'Couldn’t load products — try again in a moment': 'לא הצלחנו לטעון מוצרים — נסו שוב בעוד רגע',
  '1 product': 'מוצר אחד', '{n} products': '{n} מוצרים', 'top {n} shown': 'מוצגים {n} הראשונים',
  'Nothing matches all of these filters': 'אין תוצאות לכל המסננים האלה', 'Narrow down': 'סינון', 'Size': 'גודל',
  // ---- product page
  'Back': 'חזרה', 'Couldn’t load this product': 'לא הצלחנו לטעון את המוצר', '{deal} at {chain}': '{deal} ב{chain}',
  '{price} each when you buy {n}': '{price} ליחידה בקנייה של {n}', '{price} each': '{price} ליחידה',
  'Promotion': 'מבצע', 'Online {price}': 'אונליין {price}', 'cheapest of {n} branches': 'הזול מבין {n} סניפים',
  'In your cart': 'בסל שלך', 'sold by weight': 'נמכר במשקל', 'Price at {chains}': 'מחיר ב־{chains}',
  'cheapest first': 'מהזול ליקר', 'More expensive': 'יקר יותר', 'Not sold within {n} km': 'לא נמכר ברדיוס {n} ק״מ',
  'Not on sale right now': 'לא נמכר כרגע', 'Price history': 'היסטוריית מחירים', 'Tracking since {day}.': 'במעקב מאז {day}.',
  'Cheapest shelf price over time at each chain': 'מחיר המדף הזול לאורך זמן בכל רשת',
  'From {price} at {chain}': 'החל מ־{price} ב{chain}',
  // ---- statuses of a price
  'Not compared': 'לא מושווה', 'has it': 'יש', 'all {n} products': 'כל {n} המוצרים', 'all {n}': 'כל ה־{n}',
  '{a} of {n} products': '{a} מתוך {n} מוצרים', '{a} of {n}': '{a} מתוך {n}', 'Prices from {when}': 'מחירים נכון ל־{when}',
  'Price looks wrong': 'המחיר נראה שגוי', 'Name not checked': 'השם לא נבדק', 'Being checked': 'בבדיקה',
  'Named differently': 'בשם אחר', 'Not confirmed': 'לא מאומת', '{n} for {price}': '{n} ב־{price}',
  'Deal not counted': 'המבצע לא נספר', 'not on sale anywhere right now': 'לא נמכר כרגע באף מקום',
  'Not at {chain}': 'אין ב{chain}', 'Not counted': 'לא נספר', 'Similar': 'דומה', 'deal': 'מבצע', 'Deal details': 'פרטי המבצע', 'Regular price {p}': 'מחיר רגיל {p}',
  'Not sold here': 'לא נמכר כאן', 'not sold here': 'לא נמכר כאן', 'Missing': 'חסר', 'Club': 'מועדון',
  // ---- the cart
  'Weekly shop': 'קניות שבועיות', 'Remove': 'הסרה', 'Quantity': 'כמות', 'prices at {chain}': 'מחירים ב{chain}',
  'Finding the cheapest store…': 'מחפשים את החנות הזולה…',
  'No store within {n} km has these products.': 'אין חנות ברדיוס {n} ק״מ עם המוצרים האלה.',
  'No store has these products.': 'אין חנות עם המוצרים האלה.', 'Change distance': 'שינוי מרחק',
  'All branches': 'כל הסניפים', '{n}, cheapest first': '{n}, מהזול ליקר', 'Missing some products': 'חסרים חלק מהמוצרים',
  'Cheapest': 'הכי זול', 'Cheapest stores': 'החנויות הזולות', 'top {a} of {n}': '{a} הראשונות מתוך {n}',
  'Delivered · fee not set': 'משלוח · דמי המשלוח לא הוגדרו', 'Free delivery': 'משלוח חינם',
  'incl. {fee} delivery': 'כולל משלוח {fee}', "Couldn't save the cart": 'לא הצלחנו לשמור את הסל',
  'Removed {name}': '{name} הוסר', 'item': 'פריט', 'Undo': 'ביטול', 'New cart': 'סל חדש', '+ New cart': '+ סל חדש', 'Cart {n}': 'סל {n}',
  "Couldn't create a cart": 'לא הצלחנו ליצור סל', 'Untitled': 'ללא שם', 'Emptied {cart}': 'הסל "{cart}" רוקן',
  'Made “{cart}”': 'נוצר "{cart}"', 'Delete the cart “{cart}”?': 'למחוק את הסל "{cart}"?', 'Delete': 'מחיקה',
  'Cart deleted': 'הסל נמחק', 'Started the cart “{cart}”': 'פתחנו את הסל "{cart}"',
  'Added {n} to {cart}': 'נוספו {n} לסל "{cart}"', 'Added to {cart}': 'נוסף לסל "{cart}"', 'View cart': 'לסל',
  'Your cart': 'הסל שלך', 'Your carts': 'הסלים שלך', 'Close the cart': 'סגירת הסל', 'Cart name': 'שם הסל',
  'Compare your carts': 'השוואת הסלים שלך', 'Empty this cart': 'ריקון הסל', 'Duplicate this cart': 'שכפול הסל',
  'Delete this cart': 'מחיקת הסל', 'This cart is empty': 'הסל ריק', 'Where this cart costs least': 'איפה הסל הכי זול',
  'Every branch near you, cheapest first': 'כל הסניפים לידך, מהזול ליקר', 'Compare stores': 'השוואת חנויות', 'Go to comparison': 'מעבר להשוואה',
  // ---- checkout
  'Where to buy': 'איפה לקנות', 'How you shop': 'איך קונים', 'In store': 'בחנות', 'Delivered home': 'משלוח הביתה',
  'Club prices': 'מחירי מועדון', 'Delivery fees': 'דמי משלוח', 'Compare Stores': 'השוואת חנויות',
  "Couldn't compare prices — is the server up?": 'לא הצלחנו להשוות מחירים — השרת פועל?', 'Branch {n}': 'סניף {n}',
  'Online store': 'חנות אונליין', '{d} away': 'במרחק {d}', '{n} min drive now': '{n} דק׳ נסיעה עכשיו', '{n} min drive': '{n} דק׳ נסיעה',
  'Driving time in current traffic (Google Maps)': 'זמן נסיעה בתנועה הנוכחית (Google Maps)',
  'Typical driving time, without live traffic': 'זמן נסיעה רגיל, בלי תנועה בזמן אמת',
  'No online shop in the price files': 'אין חנות אונליין בקובצי המחירים', 'Groceries online': 'קנייה אונליין',
  'Delivery area not confirmed': 'אזור המשלוח לא אושר', 'Delivery fee not set': 'דמי המשלוח לא הוגדרו',
  'Delivery': 'משלוח', 'Free': 'חינם', 'Delivered total': 'סה״כ עם משלוח', 'Online vs in store': 'אונליין מול חנות',
  'Same prices': 'אותם מחירים', '{price} dearer': 'יקר ב־{price}', '{price} cheaper': 'זול ב־{price}',
  '{part} items here': '{part} פריטים כאן', 'Your item here': 'המוצר שלך כאן', 'All {n} items here': 'כל {n} הפריטים כאן',
  '{price} less than {chain}': '{price} פחות מ{chain}', 'Other branches:': 'סניפים נוספים:',
  'Prices on the chain’s website': 'מחירים באתר הרשת', 'Cheapest of {n} branches': 'הזול מבין {n} סניפים',
  'Only branch within {n} km': 'הסניף היחיד ברדיוס {n} ק״מ', 'Only branch with prices': 'הסניף היחיד עם מחירים',
  'Online prices': 'מחירי אונליין', 'In-store prices': 'מחירים בחנות', '{n} on promotion': '{n} במבצע', 'same': 'אותו מחיר',
  'Prices over a day old': 'מחירים בני יותר מיום', 'Online': 'אונליין', 'No current prices': 'אין מחירים עדכניים',
  'Prices are still downloading': 'המחירים עדיין נטענים', 'Your cart is empty': 'הסל שלך ריק',
  'No confirmed delivery': 'אין משלוח מאושר', 'No online shop has these products': 'אין חנות אונליין עם המוצרים האלה',
  'No stores within {n} km': 'אין חנויות ברדיוס {n} ק״מ', 'No store has these products': 'אין חנות עם המוצרים האלה',
  '{a} with your product': '{a} עם המוצר שלך', '{a} with all {n} products': '{a} עם כל {n} המוצרים',
  'Next cheapest': 'הבאות בתור', 'More expensive stores': 'חנויות יקרות יותר', 'More': 'עוד',
  '30 days ago {price}': 'לפני 30 יום: {price}', 'Date': 'תאריך', 'This cart over time at {chains}': 'הסל הזה לאורך זמן ב{chains}',
  'Compare at least two stores': 'צריך לפחות שתי חנויות להשוואה', 'Up to 5 stores side by side': 'עד 5 חנויות זו לצד זו',
  'Product': 'מוצר', 'Cheapest for': 'הכי זול עבור', 'Whole cart': 'כל הסל',
  'Buying each product where it’s cheapest among these stores comes to {total} — {save} less than all of it at {chain}, if the extra stop is worth it to you.':
    'קנייה של כל מוצר איפה שהוא הכי זול מבין החנויות האלה יוצאת {total} — {save} פחות מהכול ב{chain}, אם העצירה הנוספת שווה לך.',
  // ---- the store card
  'Looking up opening hours…': 'בודקים שעות פתיחה…', 'Today {hours}': 'היום {hours}', 'Open now': 'פתוח עכשיו',
  'Closed now': 'סגור עכשיו', 'All week': 'כל השבוע',
  'Couldn’t check the opening hours just now': 'לא הצלחנו לבדוק את שעות הפתיחה כרגע',
  'Opening hours aren’t listed for this branch': 'שעות הפתיחה של הסניף לא רשומות',
  'see them on Google Maps': 'אפשר לראות אותן ב־Google Maps',
  '{a} of {n} products here': '{a} מתוך {n} מוצרים כאן', 'for your product': 'למוצר שלך',
  'Address not listed': 'הכתובת לא רשומה', 'Finding the address…': 'מאתרים את הכתובת…', '{n} km by road': '{n} ק״מ בכביש',
  'Opening hours: OpenStreetMap': 'שעות פתיחה: OpenStreetMap', 'Open in Waze': 'פתיחה ב־Waze',
  'Open in Google Maps': 'פתיחה ב־Google Maps',
  // ---- settings and dialogs
  "Couldn't save your settings": 'לא הצלחנו לשמור את ההגדרות', 'updating {n}%': 'מתעדכן {n}%', 'updating…': 'מתעדכן…',
  'No prices yet': 'עדיין אין מחירים', 'Delivery fee': 'דמי משלוח', 'Lower fee': 'דמי משלוח מוזלים',
  '…for baskets over': '…לסל מעל', 'Free over': 'חינם מעל', 'Minimum order': 'מינימום הזמנה', 'Reset': 'איפוס',
  'Chain': 'רשת', 'Delivers to me': 'משלוח אליי', '{chain} delivers to me': '{chain} שולחת אליי',
  "Couldn't save delivery fees": 'לא הצלחנו לשמור את דמי המשלוח', "Couldn't load delivery fees": 'לא הצלחנו לטעון את דמי המשלוח',
  'Not in the chains’ price files — edit to match their sites. Saved as you type.':
    'לא מופיע בקובצי המחירים של הרשתות — עדכנו לפי האתרים שלהן. נשמר תוך כדי הקלדה.',
  'Compare carts': 'השוואת סלים', 'Make another cart to compare.': 'צרו סל נוסף כדי להשוות.', 'Up to 4 carts': 'עד 4 סלים',
  'Pick two or more.': 'בחרו שניים או יותר.', 'Couldn’t price these carts.': 'לא הצלחנו לתמחר את הסלים.',
  'All together': 'הכול יחד', 'Close': 'סגירה', 'Done': 'סיום', 'Cancel': 'ביטול', 'Show stores within': 'הצגת חנויות ברדיוס',
  // ---- signing in
  'Supermarket price comparison': 'השוואת מחירים בסופרמרקטים', 'Continue with {name}': 'המשך עם {name}',
  'Sign-in was cancelled.': 'הכניסה בוטלה.', 'Couldn’t sign you in. Try again.': 'לא הצלחנו להכניס אותך. נסו שוב.',
  'You were signed out. Sign in again.': 'יצאת מהחשבון. היכנסו שוב.',
  'Sal-Kal isn’t reachable right now. Try again in a minute.': 'סל קל לא זמין כרגע. נסו שוב בעוד דקה.', 'Try again': 'ניסיון נוסף',
  'Deleting your data': 'מחיקת המידע שלך', 'Sign-in opens soon.': 'הכניסה תיפתח בקרוב.', 'Built by': 'נבנה על ידי', 'About': 'אודות',
  // ---- the address picker
  'Where do you shop from?': 'מאיפה יוצאים לקניות?', 'Use my current location': 'שימוש במיקום הנוכחי שלי',
  'or choose it': 'או בחירה מהרשימה', 'City or town': 'עיר או יישוב', 'Start typing — e.g. Ramat Gan': 'התחילו להקליד — למשל רמת גן',
  'Clear': 'ניקוי', 'Street': 'רחוב', 'Choose a street': 'בחירת רחוב', 'Number': 'מספר', 'No street names here': 'אין כאן שמות רחובות',
  'Finding it on the map…': 'מאתרים במפה…', 'Placed on the street': 'סומן על הרחוב', 'Placed in the town centre': 'סומן במרכז היישוב',
  'Type to see more': 'הקלידו כדי לראות עוד', 'No match — check the spelling': 'לא נמצא — בדקו את האיות', 'Start typing': 'התחילו להקליד',
  'Couldn’t find this address on the map just now. Try again in a minute.': 'לא הצלחנו לאתר את הכתובת במפה כרגע. נסו שוב בעוד דקה.',
  'Location isn’t available. Allow it in your browser, or choose the address below.': 'המיקום לא זמין. אפשרו אותו בדפדפן, או בחרו כתובת למטה.',
  'That location isn’t an address in Israel we can use. Choose it below instead.': 'המיקום הזה אינו כתובת בישראל שאפשר להשתמש בה. בחרו כתובת למטה.',
  'Address saved': 'הכתובת נשמרה',
  // ---- first run
  'Step 1 of 3': 'שלב 1 מתוך 3', 'Step 2 of 3': 'שלב 2 מתוך 3', 'Step 3 of 3': 'שלב 3 מתוך 3',
  'How far will you go?': 'כמה רחוק מוכנים לנסוע?', 'Carts': 'סלים', 'A cart is a list you name': 'סל הוא רשימה עם שם',
  'Weekly shop, Shabbat, a party — keep as many as you like.': 'קניות שבועיות, שבת, מסיבה — כמה שרוצים.',
  'Add products': 'מוסיפים מוצרים', 'Search by name or barcode.': 'חיפוש לפי שם או ברקוד.', 'Compare': 'משווים',
  'See where each cart costs least within {n} km of {place}.': 'רואים איפה כל סל הכי זול, עד {n} ק״מ מ{place}.',
  'Name your first cart': 'שם לסל הראשון', 'Continue': 'המשך', 'Start shopping': 'מתחילים',
  'Couldn’t save — try again': 'לא נשמר — נסו שוב',
  // ---- account
  'Settings': 'הגדרות', 'Account': 'חשבון', 'Address': 'כתובת', 'Not set': 'לא הוגדרה', 'Distance': 'מרחק',
  'Any distance': 'כל מרחק', 'Drive times': 'זמני נסיעה',
  'Drive times: live traffic (Google Maps). Save an empty key for typical times.': 'זמני נסיעה: תנועה בזמן אמת (Google Maps). שמירת מפתח ריק חוזרת לזמנים רגילים.',
  'Drive times: typical, without traffic. A Google Maps key (Routes API) adds live traffic.': 'זמני נסיעה: רגילים, בלי תנועה. מפתח Routes API של Google Maps מוסיף תנועה בזמן אמת.',
  'Google Maps API key (Routes API)': 'מפתח Routes API של Google Maps', 'Google Maps API key': 'מפתח Google Maps API',
  'Save key': 'שמירת מפתח', 'Sign out': 'יציאה מהחשבון', 'Delete my account': 'מחיקת החשבון שלי',
  'Delete your account? Your carts, address and settings are erased for good.': 'למחוק את החשבון? הסלים, הכתובת וההגדרות שלך יימחקו לצמיתות.',
  'Couldn’t delete the account — try again': 'לא הצלחנו למחוק את החשבון — נסו שוב', 'Live traffic is on': 'תנועה בזמן אמת פועלת',
  'Using typical drive times': 'משתמשים בזמני נסיעה רגילים', "Couldn't save the key": 'לא הצלחנו לשמור את המפתח',
  // ---- installing the app (pwa.js)
  'Install app': 'התקנת האפליקציה', 'Installed': 'האפליקציה הותקנה', 'Share': 'שיתוף', 'or under ⋯': 'או תחת ⋯',
  'Add to Home Screen': 'הוספה למסך הבית', 'File › Add to Dock': 'קובץ ‹ הוספה ל־Dock',
  'Browser menu': 'תפריט הדפדפן', 'or Add to Home screen': 'או הוספה למסך הבית',
};

// counted nouns: [one, many] in English; [one, many, "one X" written out] in Hebrew ("מוצר אחד", "3 מוצרים")
const NOUNS = {
  product: [['product', 'products'], ['מוצר', 'מוצרים', 'מוצר אחד']],
  stop: [['stop', 'stops'], ['עצירה', 'עצירות', 'עצירה אחת']],
  item: [['item', 'items'], ['פריט', 'פריטים', 'פריט אחד']],
  chain: [['chain', 'chains'], ['רשת', 'רשתות', 'רשת אחת']],
  'other chain': [['other chain', 'other chains'], ['רשת נוספת', 'רשתות נוספות', 'רשת נוספת אחת']],
  branch: [['branch', 'branches'], ['סניף', 'סניפים', 'סניף אחד']],
  'online shop': [['online shop', 'online shops'], ['חנות אונליין', 'חנויות אונליין', 'חנות אונליין אחת']],
  store: [['store', 'stores'], ['חנות', 'חנויות', 'חנות אחת']],
};

// chain names as Israelis write them (the server and the charts keep the English ones as ids)
const CHAINS_HE = {
  'Shufersal': 'שופרסל', 'Rami Levy': 'רמי לוי', 'Victory': 'ויקטורי', 'Yohananof': 'יוחננוף', 'Osher Ad': 'אושר עד',
  'Tiv Taam': 'טיב טעם', 'Hazi Hinam': 'חצי חינם', 'Keshet Teamim': 'קשת טעמים', 'Super-Pharm': 'סופר-פארם',
  'King Store': 'קינג סטור', 'Carrefour': 'קרפור', 'Yeinot Bitan': 'יינות ביתן', 'Mahsanei HaShuk': 'מחסני השוק',
  'Yellow': 'יילו', 'Wolt Market': 'וולט מרקט', 'Fresh Market': 'פרש מרקט', 'Good Pharm': 'גוד פארם',
  'Salach Dabach': 'סאלח דבאח', 'Zol Ubegadol': 'זול ובגדול', 'Dor Alon': 'דור אלון', 'Bareket': 'ברקת',
  'Polizer': 'פוליצר', 'Super Yuda': 'סופר יודה', 'Super Sapir': 'סופר ספיר', 'Shuk HaIr': 'שוק העיר',
  'Netiv HaHesed': 'נתיב החסד', 'Maayan 2000': 'מעיין 2000', 'Shefa Birkat Hashem': 'שפע ברכת השם', 'H. Cohen': 'ח. כהן',
  'City Market': 'סיטי מרקט', 'City Market Kiryat Gat': 'סיטי מרקט קריית גת', 'Mishnat Yosef': 'משנת יוסף',
  'Stop Market': 'סטופ מרקט',
};

function t(s, vars) {
  let out = (LANG === 'he' && HE[s]) || s;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return out;
}
function tn(n, noun) {
  const e = NOUNS[noun];
  if (!e) return `${n} ${noun}`;
  if (LANG === 'he') return n === 1 ? e[1][2] : `${Number(n).toLocaleString('he-IL')} ${e[1][1]}`;
  return `${Number(n).toLocaleString('en-GB')} ${n === 1 ? e[0][0] : e[0][1]}`;
}
const cn = name => (LANG === 'he' && CHAINS_HE[name]) || name || '';
const catName = c => (LANG === 'he' ? c.he || c.name : c.name);
const perKg = () => '/' + t('kg');
// a small flag beside each language's name (drawn, since flag emoji show as letters on Windows)
const FLAG = {
  he: '<svg class="flag" viewBox="0 0 220 160" aria-hidden="true"><rect width="220" height="160" fill="#fff"/><rect y="16" width="220" height="24" fill="#0038b8"/><rect y="120" width="220" height="24" fill="#0038b8"/><g fill="none" stroke="#0038b8" stroke-width="6"><path d="M110 50 137 97H83z"/><path d="M110 110 83 63h54z"/></g></svg>',
  en: '<svg class="flag" viewBox="0 0 247 130" preserveAspectRatio="none" aria-hidden="true"><rect width="247" height="130" fill="#b22234"/><g fill="#fff"><rect y="10" width="247" height="10"/><rect y="30" width="247" height="10"/><rect y="50" width="247" height="10"/><rect y="70" width="247" height="10"/><rect y="90" width="247" height="10"/><rect y="110" width="247" height="10"/></g><rect width="99" height="70" fill="#3c3b6e"/><g fill="#fff"><circle cx="16" cy="14" r="4"/><circle cx="40" cy="14" r="4"/><circle cx="64" cy="14" r="4"/><circle cx="88" cy="14" r="4"/><circle cx="28" cy="35" r="4"/><circle cx="52" cy="35" r="4"/><circle cx="76" cy="35" r="4"/><circle cx="16" cy="56" r="4"/><circle cx="40" cy="56" r="4"/><circle cx="64" cy="56" r="4"/><circle cx="88" cy="56" r="4"/></g></svg>',
};
const LANG_NAME = { he: 'עברית', en: 'English' };
// a name inside a sentence of the other language keeps its own direction (U+2068 … U+2069)
const iso = s => `\u2068${s}\u2069`;

const UNITS_HE = { g: 'גרם', kg: 'ק״ג', ml: 'מ״ל', L: 'ליטר' };
// "400 g", "1.5 L", "100 g" (unit prices) in the page's language
const sizeText = s => (LANG === 'he' && s ? String(s).replace(/\b(kg|g|ml|L)\b/g, u => UNITS_HE[u]) : s || '');
// opening hours from the server: "07:00–23:00", "Closed", "Open 24 hours"
const hoursText = s => (LANG !== 'he' ? s : s === 'Closed' ? 'סגור' : s === 'Open 24 hours' ? 'פתוח 24 שעות' : s);

const MONTHS_HE = { January: 'ינואר', February: 'פברואר', March: 'מרץ', April: 'אפריל', May: 'מאי', June: 'יוני', July: 'יולי',
  August: 'אוגוסט', September: 'ספטמבר', October: 'אוקטובר', November: 'נובמבר', December: 'דצמבר' };
// notes the server writes in English (delivery rules from app/chains.py and app/engine.py)
function serverText(s) {
  if (!s || LANG !== 'he') return s || '';
  if (HE[s]) return HE[s];
  const m = s.replace(/\b(January|February|March|April|May|June|July|August|September|October|November|December)\b/g, x => MONTHS_HE[x]);
  const rules = [
    [/^No home delivery$/, 'אין משלוחים הביתה'],
    [/^Below the ₪([\d.]+) minimum order$/, 'מתחת למינימום הזמנה של ₪$1'],
    [/^Raised to ([\d.]+) in (\S+ )?(\d{4})$/, (x, a, mo, y) => (mo ? `עלה ל־${a} ב${mo}${y}` : `עלה ל־${a} ב־${y}`)],
    [/^([\d.]+) as of (\S+) (\d{4})$/, '$1 נכון ל$2 $3'],
    [/^Free above ([\d.]+) \((\S+) (\d{4})\)$/, 'חינם מעל $1 ($2 $3)'],
    [/^([\d.]+) above ([\d.]+) \((\S+) (\d{4})\)$/, '$1 מעל $2 ($3 $4)'],
    [/^Pickup only as of (\S+) (\d{4})$/, 'איסוף עצמי בלבד, נכון ל$1 $2'],
  ];
  for (const [rx, to] of rules) if (rx.test(m)) return m.replace(rx, to);
  return m;
}

function applyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-ph]').forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
  root.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
  root.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.title = t('Sal-Kal');
}
function switchLang(l) {
  LANG = l === 'en' ? 'en' : 'he';
  try { localStorage.setItem('lang', LANG); } catch { /* private mode */ }
  document.cookie = `lang=${LANG};path=/;max-age=31536000;samesite=lax`;
  document.documentElement.lang = LANG;
  document.documentElement.dir = rtl() ? 'rtl' : 'ltr';
  applyI18n();
}
document.documentElement.lang = LANG;
document.documentElement.dir = rtl() ? 'rtl' : 'ltr';
document.cookie = `lang=${LANG};path=/;max-age=31536000;samesite=lax`;
