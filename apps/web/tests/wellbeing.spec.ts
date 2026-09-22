import {test,expect} from '@playwright/test';
import {warmFixture} from './warm-theme-fixture';
for(const width of [390,844]) test(`wellbeing check-in save edit delete ${width}`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:390});await warmFixture(page);let rows:any[]=[];
 await page.route('**/api/wellbeing**',async route=>{const req=route.request();const body=req.method()==='GET'?{}:req.postDataJSON();let value:any={items:rows,partial:false};
 if(req.method()==='POST'){rows=[{...body,id:body.requestId,revision:1,createdAt:1790087000}];value=rows[0];}
 if(req.method()==='PATCH'){rows=[{...rows[0],...body,revision:2}];value=rows[0];}
 if(req.method()==='DELETE'){rows=[];value={deleted:true};}
 await route.fulfill({json:value});});
 await page.goto('/?view=today#today/wellbeing/2026-09-22');
 await expect(page.getByRole('heading',{name:'A moment for you'})).toBeVisible();
 await expect(page.getByText('A space for how your day feels to you.', {exact:false})).toBeVisible();
 await expect(page.getByText('No check-ins saved for this date. You can check in whenever you like.')).toBeVisible();
 await page.getByRole('group',{name:'How are you feeling?'}).getByRole('radio',{name:'Good',exact:true}).check();
 await page.getByRole('textbox',{name:'Anything you want to note?'}).fill('A quiet morning');
 await page.getByRole('button',{name:'Save check-in',exact:true}).click();
 await expect(page.locator('.wellbeing-page').getByRole('status')).toHaveText('Your check-in is saved.');
 await expect(page.locator('.wellbeing-entry')).toContainText('A quiet morning');
 await page.reload();await expect(page.locator('.wellbeing-entry')).toContainText('A quiet morning');
 await page.locator('.wellbeing-entry').getByRole('button',{name:'Edit',exact:true}).click();
 await page.getByRole('textbox',{name:'Anything you want to note?'}).fill('A calmer morning');await page.getByRole('button',{name:'Save changes'}).click();
 await expect(page.locator('.wellbeing-entry')).toContainText('A calmer morning');
 page.on('dialog',dialog=>dialog.accept());await page.locator('.wellbeing-entry').getByRole('button',{name:'Delete',exact:true}).click();
 await expect(page.locator('.wellbeing-entry')).toHaveCount(0);
});
